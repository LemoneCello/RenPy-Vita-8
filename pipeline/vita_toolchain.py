# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Toolchain stage: set up everything vita_port_build.py needs to build the eboot.

Builds into build/toolchain/ in the workspace (the current directory), with
pinned versions:

  vitasdk     VitaSDK (series 2026.08) from vdpm's bootstrap
  packages    the vdpm library packages the port links
  pvr         PVR_PSP2 v3.9: GLES/EGL headers and stub libraries
  hostpy      host Python 3.11 with Cython 0.29.37 (uv)
  libjpeg     libjpeg-turbo 3.2.0 with NEON
  zlib        zlib-ng 2.2.4 in zlib-compatible mode with NEON (libz.a only)
  sdl2        SDL2 2.32.8 with the PVR video driver, plus patches
  sdl2_image  SDL2_image 2.8.12 without stb_image, plus patches
  harfbuzz    HarfBuzz 14.5.0, plus the deadlock patch
  ffmpeg      FFmpeg 9.0.2 without -ffast-math (patches/ffmpeg-9.0.2-vita-build.sh)
  cpython     Grimiku's cpython-vita (3.11) at -O2, plus patches

The rebuilt libraries replace vdpm's versions in the SDK. Each step leaves a
marker in build/toolchain/.done/ holding a fingerprint of its recipe
(versions, flags, patches); later runs skip finished steps and redo a step
whose recipe changed, and every step after it.

  uv run RenPy-Vita-8/pipeline/vita_toolchain.py              # everything
  uv run RenPy-Vita-8/pipeline/vita_toolchain.py --from sdl2  # redo from a step
  uv run RenPy-Vita-8/pipeline/vita_toolchain.py --dir build/toolchain-test

Needs the container's build tools (see container/Dockerfile): git, curl,
cmake, ninja, make, autotools, patch, unzip, and uv. Downloads are checked
against known SHA-256 sums. After a step changes a library, rebuild the
eboot from scratch (vita_port_build.py doesn't notice changed SDK libraries;
delete build/toolchain/port-work/<label>/renpy-vita/build/eboot.elf).
"""

import argparse
import hashlib
import inspect
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHES = HERE / "patches"

VDPM = ("https://github.com/vitasdk/vdpm", "c9ba33bd428ba88052f79d78592f4b318a7a322b")
VITASDK_CHANNEL = "2026.08"

# What the port links; installed with vdpm. The versions the eboot was
# developed with, for a warning when vdpm's channel has moved on.
PACKAGES = {
    "taihen": "0.11-1", "sdl2_mixer": "2.8.2-1", "sdl2_image": "2.8.12-1", "sdl2_ttf": "2.24.0-1",
    "sdl2_gfx": "1.0.4-2", "ffmpeg": "9.0.2-1", "libwebp": "1.6.0-1", "libvorbis": "1.3.7-3",
    "libogg": "1.3.6-2", "mpg123": "1.33.7-1", "flac": "1.3.4-1", "libmikmod": "3.3.14-1",
    "opus": "1.6.1-1", "lame": "4.0-1", "freetype": "2.14.3-1", "harfbuzz": "14.5.0-1",
    "libjpeg-turbo": "3.2.0-1", "libpng": "1.6.58-1", "bzip2": "1.0.8-1",
    "openssl-1.1.1": "1.1.1.r24282.g0a851f7-2", "zlib": "1.3.2-2", "libintl": "0.22.5-1",
    "fribidi": "1.0.17-1", "libzip": "1.11.4-2", "xz": "5.8.4-1",
}

PVR_REPO = ("https://github.com/GrapheneCt/PVR_PSP2", "ae4df4b723f3f1aa629b1f3b276b9cca6d663891")
PVR_STUBS = ("https://github.com/GrapheneCt/PVR_PSP2/releases/download/v3.9/vitasdk_stubs.zip",
             "7ee2498b58cb97871fcb0e3e134ce1045acf2c22ce4873b1844a391b5da4fe47")

CYTHON = "0.29.37"

LIBJPEG = ("https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.2.0/libjpeg-turbo-3.2.0.tar.gz",
           "6f30092cef9fb839779646608f4ee14ae3cbac989c47fa05e841b0841f09878e")
ZLIBNG = ("https://github.com/zlib-ng/zlib-ng/archive/refs/tags/2.2.4.tar.gz",
          "a73343c3093e5cdc50d9377997c3815b878fd110bf6511c2c7759f2afb90f5a3", "zlib-ng-2.2.4.tar.gz")
SDL2 = ("https://github.com/libsdl-org/SDL/releases/download/release-2.32.8/SDL2-2.32.8.tar.gz",
        "0ca83e9c9b31e18288c7ec811108e58bac1f1bb5ec6577ad386830eac51c787e")
SDL2_IMAGE = ("https://www.libsdl.org/projects/SDL_image/release/SDL2_image-2.8.12.tar.gz",
              "393f5efb50536ec13ca4f4affb69cc9966d3c3f969e6c5e701faddf9f9785381")
# vdpm's recipe applies this to SDL2_image (vitasdk/packages).
SDL2_IMAGE_PKGCONFIG = ("https://raw.githubusercontent.com/vitasdk/packages/de769c80482cc3e53cc16b71cb4bb27a323e5a31/sdl2_image/pkg-config-fix.patch",
                        "a35580ce63abb881753d5751c1fdb61c4276ddce1253125654d9a8b082a1aa58")
HARFBUZZ = ("https://github.com/harfbuzz/harfbuzz/releases/download/14.5.0/harfbuzz-14.5.0.tar.xz",
            "b7132e148358a45185c9feafd049dbaf243649d3c44414b3534d9c95d18592b9")
FFMPEG = ("https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz",
          "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e")
CPYTHON = ("https://github.com/Grimiku/cpython-vita", "5dd178c51a7c844ba50279aa43eb82a3abf50b1f")

# The flags vdpm's recipes use with GCC 15, which errors on old-C idioms.
CFLAGS = "-std=gnu11 -Wno-error=implicit-function-declaration -Wno-error=int-conversion -Wno-error=incompatible-pointer-types"
CXXFLAGS = "-std=gnu++11 -Wno-error=implicit-function-declaration -Wno-error=int-conversion -Wno-error=incompatible-pointer-types"


class Toolchain:

    def __init__(self, root, jobs):
        self.root = root.resolve()
        self.src = self.root / "src"
        self.downloads = self.root / "downloads"
        self.sdk = self.root / "vitasdk"
        self.jobs = str(jobs)
        self.env = dict(os.environ, VITASDK=str(self.sdk),
                        PATH=f"{self.root / 'hostpy/bin'}:{self.sdk / 'bin'}:{os.environ['PATH']}")

    # Helpers.

    def run(self, cmd, cwd=None, **kwargs):
        print("+", cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd), f"(in {cwd})" if cwd else "", flush=True)
        subprocess.run(cmd, cwd=cwd, env=self.env, check=True, shell=isinstance(cmd, str), **kwargs)

    def fetch(self, url, sha256, name=None):
        path = self.downloads / (name or url.rsplit("/", 1)[1])
        if not path.exists():
            self.downloads.mkdir(parents=True, exist_ok=True)
            print(f"downloading {url}", flush=True)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sha256:
            sys.exit(f"{path}: SHA-256 {digest}, expected {sha256} (delete it to download again)")
        return path

    def unpack(self, archive, name):
        """A fresh copy of the sources in src/NAME."""
        dest = self.src / name
        if dest.exists():
            shutil.rmtree(dest)
        self.src.mkdir(parents=True, exist_ok=True)
        tmp = self.src / f".unpack-{name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir()
        self.run(["tar", "xf", archive, "-C", tmp])
        (top,) = tmp.iterdir()
        top.rename(dest)
        tmp.rmdir()
        return dest

    def checkout(self, repo, commit, name):
        dest = self.src / name
        if not (dest / ".git").exists():
            if dest.exists():
                shutil.rmtree(dest)
            self.src.mkdir(parents=True, exist_ok=True)
            self.run(["git", "clone", "-q", repo, dest])
        self.run(["git", "checkout", "-q", "--force", commit], cwd=dest)
        self.run(["git", "clean", "-q", "-fdx"], cwd=dest)
        return dest

    def patch(self, tree, *patches):
        for p in patches:
            self.run(["patch", "-Np1", "-i", p], cwd=tree, stdout=subprocess.DEVNULL)

    def cmake(self, tree, *args, install=True):
        build = tree / "build-vita"
        self.run(["cmake", "-S", tree, "-B", build, "-G", "Ninja",
                  f"-DCMAKE_TOOLCHAIN_FILE={self.sdk / 'share/vita.toolchain.cmake'}",
                  f"-DCMAKE_INSTALL_PREFIX={self.sdk / 'arm-vita-eabi'}",
                  "-DCMAKE_BUILD_TYPE=Release", *args], stdout=subprocess.DEVNULL)
        self.run(["ninja", "-C", build, "-j", self.jobs], stdout=subprocess.DEVNULL)
        if install:
            self.run(["ninja", "-C", build, "install"], stdout=subprocess.DEVNULL)
        return build

    # Steps.

    def step_vitasdk(self):
        vdpm = self.checkout(*VDPM, "vdpm")
        if self.sdk.exists():
            shutil.rmtree(self.sdk)
        self.env["VITASDK_CHANNEL"] = VITASDK_CHANNEL
        try:
            self.run(["bash", vdpm / "bootstrap-vitasdk.sh", "--install-dir", self.sdk])
        finally:
            del self.env["VITASDK_CHANNEL"]

    def step_packages(self):
        self.run(f"yes | vdpm install {' '.join(PACKAGES)}", stdout=subprocess.DEVNULL)
        listing = subprocess.run(["vdpm", "list"], env=self.env, capture_output=True, text=True, check=True).stdout
        installed = dict(l.split()[:2] for l in listing.splitlines() if len(l.split()) == 2)
        for name, version in PACKAGES.items():
            have = installed.get(name)
            if have is None:
                sys.exit(f"vdpm didn't install {name}")
            if have != version:
                print(f"WARNING: {name} {have} (the eboot was developed with {version})")

    def step_pvr(self):
        repo = self.checkout(*PVR_REPO, "PVR_PSP2")
        include = self.sdk / "arm-vita-eabi/include"
        for d in ("EGL", "GLES", "GLES2", "KHR", "gpu_es4"):
            shutil.copytree(repo / "include" / d, include / d, dirs_exist_ok=True)
        stubs = self.src / "pvr-stubs"
        if stubs.exists():
            shutil.rmtree(stubs)
        stubs.mkdir(parents=True)
        self.run(["unzip", "-q", "-o", self.fetch(*PVR_STUBS), "-d", stubs])
        # The zip has a folder per library (named like an .a file) holding
        # liblib<name>_stub.a and liblib<name>_stub_weak.a.
        for a in stubs.glob("*/*.a"):
            shutil.copy2(a, self.sdk / "arm-vita-eabi/lib" / a.name)

    def step_hostpy(self):
        venv = self.root / "hostpy"
        self.run(["uv", "venv", "-q", "--clear", "--python", "3.11", venv])
        self.run(["uv", "pip", "install", "-q", "--python", venv / "bin/python",
                  f"Cython=={CYTHON}", "setuptools", "wheel", "future"])

    def step_libjpeg(self):
        # vdpm's recipe, but with SIMD (NEON): JPEG decoding ~40% faster.
        tree = self.unpack(self.fetch(*LIBJPEG), "libjpeg-turbo")
        self.cmake(tree, "-DENABLE_SHARED=FALSE", "-DWITH_SIMD=TRUE", "-DWITH_TOOLS=FALSE")

    def step_zlib(self):
        # A drop-in libz.a (zlib API, NEON inflate/adler32/slide_hash). Runtime
        # CPU detection can't work on the Vita (no /proc, no getauxval), and
        # ACLE CRC32 is ARMv8, which the Cortex-A9 lacks. Only the library
        # replaces the SDK's; the stock headers stay (same ABI in compat mode).
        tree = self.unpack(self.fetch(*ZLIBNG), "zlib-ng")
        build = self.cmake(tree, "-DZLIB_COMPAT=ON", "-DZLIB_ENABLE_TESTS=OFF", "-DZLIBNG_ENABLE_TESTS=OFF",
                           "-DWITH_GTEST=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DWITH_RUNTIME_CPU_DETECTION=OFF",
                           "-DWITH_ACLE=OFF", install=False)
        shutil.copy2(build / "libz.a", self.sdk / "arm-vita-eabi/lib/libz.a")

    def step_sdl2(self):
        tree = self.unpack(self.fetch(*SDL2), "SDL2")
        self.patch(tree, PATCHES / "sdl2-2.32.8-vita-pvr-ime.patch", PATCHES / "sdl2-2.32.8-vita-stdio-buffer.patch")
        self.cmake(tree, "-DSDL_TEST=OFF", "-DVIDEO_VITA_PVR=ON", f"-DCMAKE_C_FLAGS={CFLAGS}")

    def step_sdl2_image(self):
        tree = self.unpack(self.fetch(*SDL2_IMAGE), "SDL2_image")
        self.patch(tree, self.fetch(*SDL2_IMAGE_PKGCONFIG, name="sdl2_image-pkg-config-fix.patch"),
                   PATCHES / "sdl2_image-2.8.12-jpeg-rgba.patch", PATCHES / "sdl2_image-2.8.12-vita-hw-jpeg.patch")
        # vdpm's recipe, but without SDL_image's built-in stb_image, which
        # otherwise decodes PNG and JPEG (far slower than libpng/libjpeg-turbo).
        self.cmake(tree, "-DCMAKE_POSITION_INDEPENDENT_CODE=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DSDL2IMAGE_SAMPLES=OFF",
                   "-DSDL2IMAGE_WEBP=ON", "-DSDL2IMAGE_DEPS_SHARED=OFF", "-DSDL2IMAGE_AVIF=OFF", "-DSDL2IMAGE_TIF=OFF",
                   "-DSDL2IMAGE_BACKEND_STB=OFF", f"-DCMAKE_C_FLAGS={CFLAGS}", f"-DCMAKE_CXX_FLAGS={CXXFLAGS}")

    def step_harfbuzz(self):
        tree = self.unpack(self.fetch(*HARFBUZZ), "harfbuzz")
        self.patch(tree, PATCHES / "harfbuzz-hb-ft-static-face-deadlock.patch")
        self.cmake(tree, "-DHB_HAVE_FREETYPE=ON", f"-DCMAKE_C_FLAGS={CFLAGS}", f"-DCMAKE_CXX_FLAGS={CXXFLAGS}")

    def step_ffmpeg(self):
        tree = self.unpack(self.fetch(*FFMPEG), "ffmpeg")
        self.run(["sh", PATCHES / "ffmpeg-9.0.2-vita-build.sh"], cwd=tree, stdout=subprocess.DEVNULL)
        self.run(["make", f"-j{self.jobs}"], cwd=tree, stdout=subprocess.DEVNULL)
        self.run(["make", "install"], cwd=tree, stdout=subprocess.DEVNULL)

    def step_cpython(self):
        tree = self.checkout(*CPYTHON, "cpython-vita")
        self.patch(tree, PATCHES / "cpython-vita-timed-waits.patch", PATCHES / "cpython-vita-scandir-d-type.patch")
        prefix = self.sdk / "arm-vita-eabi"
        self.run(["./configure", "--build=x86_64-pc-linux-gnu", "--host=arm-vita-eabi",
                  "--with-build-python=python3.11", f"--prefix={prefix}", "--disable-test-modules",
                  "--with-ensurepip=no", "--disable-ipv6", "CONFIG_SITE=config.site", "LDFLAGS=-lpthread",
                  "CFLAGS=-std=gnu11 -Wno-error=implicit-function-declaration -Wno-error=int-conversion "
                  "-Wno-error=incompatible-pointer-types -Wl,-q,-z,nocopyreloc",
                  "CPPFLAGS=-Wl,-q,-z,nocopyreloc"], cwd=tree, stdout=subprocess.DEVNULL)
        # -O2: the default build here isn't optimised for speed.
        opt = "OPT=-DNDEBUG -g -fwrapv -O2 -Wall"
        self.run(["make", f"-j{self.jobs}", opt], cwd=tree, stdout=subprocess.DEVNULL)
        self.run(["make", "install", opt], cwd=tree, stdout=subprocess.DEVNULL)
        shutil.copy2(tree / "Modules/_decimal/libmpdec/libmpdec.a", prefix / "lib/libmpdec.a")

    STEPS = ["vitasdk", "packages", "pvr", "hostpy", "libjpeg", "zlib", "sdl2", "sdl2_image", "harfbuzz", "ffmpeg", "cpython"]

    # What each step's result depends on, for its fingerprint.
    def recipe(self, name):
        files = {
            "sdl2": ["sdl2-2.32.8-vita-pvr-ime.patch", "sdl2-2.32.8-vita-stdio-buffer.patch"],
            "sdl2_image": ["sdl2_image-2.8.12-jpeg-rgba.patch", "sdl2_image-2.8.12-vita-hw-jpeg.patch"],
            "harfbuzz": ["harfbuzz-hb-ft-static-face-deadlock.patch"],
            "ffmpeg": ["ffmpeg-9.0.2-vita-build.sh"],
            "cpython": ["cpython-vita-timed-waits.patch", "cpython-vita-scandir-d-type.patch"],
        }.get(name, [])
        h = hashlib.sha256(inspect.getsource(getattr(Toolchain, "step_" + name)).encode())
        h.update(repr((VDPM, VITASDK_CHANNEL, PACKAGES, PVR_REPO, PVR_STUBS, CYTHON, LIBJPEG, ZLIBNG, SDL2,
                       SDL2_IMAGE, SDL2_IMAGE_PKGCONFIG, HARFBUZZ, FFMPEG, CPYTHON, CFLAGS, CXXFLAGS)).encode())
        for f in files:
            h.update((PATCHES / f).read_bytes())
        return h.hexdigest()

    def marker(self, name):
        return self.root / ".done" / name


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path("build/toolchain"), help="where the toolchain goes")
    ap.add_argument("--from", dest="start", choices=Toolchain.STEPS, help="redo this step and every one after it")
    ap.add_argument("--only", choices=Toolchain.STEPS, help="redo just this step")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    tc = Toolchain(args.dir, args.jobs)
    (tc.root / ".done").mkdir(parents=True, exist_ok=True)

    redo = False
    for name in Toolchain.STEPS:
        if args.only and name != args.only:
            continue
        marker = tc.marker(name)
        recipe = tc.recipe(name)
        forced = name in (args.start, args.only)
        redo = redo or forced or not marker.exists() or marker.read_text() != recipe
        if not redo:
            print(f"== {name}: done")
            continue
        print(f"== {name}", flush=True)
        marker.unlink(missing_ok=True)
        getattr(tc, "step_" + name)()
        marker.write_text(recipe)

    print(f"\nToolchain ready in {tc.root}. Build the eboot with:\n"
          f"  uv run RenPy-Vita-8/pipeline/vita_port_build.py --label zc")


if __name__ == "__main__":
    main()
