# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
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
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from vita_common import progress

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

# CMake options per library (besides toolchain file, prefix and Release).
# vdpm's recipe for libjpeg-turbo, but with SIMD (NEON): JPEG decoding ~40% faster.
LIBJPEG_FLAGS = ["-DENABLE_SHARED=FALSE", "-DWITH_SIMD=TRUE", "-DWITH_TOOLS=FALSE"]
# A drop-in libz.a (zlib API, NEON inflate/adler32/slide_hash). Runtime CPU
# detection can't work on the Vita (no /proc, no getauxval), and ACLE CRC32
# is ARMv8, which the Cortex-A9 lacks.
ZLIB_FLAGS = ["-DZLIB_COMPAT=ON", "-DZLIB_ENABLE_TESTS=OFF", "-DZLIBNG_ENABLE_TESTS=OFF", "-DWITH_GTEST=OFF",
              "-DBUILD_SHARED_LIBS=OFF", "-DWITH_RUNTIME_CPU_DETECTION=OFF", "-DWITH_ACLE=OFF"]
SDL2_FLAGS = ["-DSDL_TEST=OFF", "-DVIDEO_VITA_PVR=ON", f"-DCMAKE_C_FLAGS={CFLAGS}"]
# vdpm's recipe, but without SDL_image's built-in stb_image, which otherwise
# decodes PNG and JPEG (far slower than libpng/libjpeg-turbo).
SDL2_IMAGE_FLAGS = ["-DCMAKE_POSITION_INDEPENDENT_CODE=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DSDL2IMAGE_SAMPLES=OFF",
                    "-DSDL2IMAGE_WEBP=ON", "-DSDL2IMAGE_DEPS_SHARED=OFF", "-DSDL2IMAGE_AVIF=OFF", "-DSDL2IMAGE_TIF=OFF",
                    "-DSDL2IMAGE_BACKEND_STB=OFF", f"-DCMAKE_C_FLAGS={CFLAGS}", f"-DCMAKE_CXX_FLAGS={CXXFLAGS}"]
HARFBUZZ_FLAGS = ["-DHB_HAVE_FREETYPE=ON", f"-DCMAKE_C_FLAGS={CFLAGS}", f"-DCMAKE_CXX_FLAGS={CXXFLAGS}"]
CPYTHON_CONFIGURE = ["--build=x86_64-pc-linux-gnu", "--host=arm-vita-eabi", "--with-build-python=python3.11",
                     "--disable-test-modules", "--with-ensurepip=no", "--disable-ipv6", "CONFIG_SITE=config.site",
                     "LDFLAGS=-lpthread",
                     "CFLAGS=-std=gnu11 -Wno-error=implicit-function-declaration -Wno-error=int-conversion "
                     "-Wno-error=incompatible-pointer-types -Wl,-q,-z,nocopyreloc",
                     "CPPFLAGS=-Wl,-q,-z,nocopyreloc"]
# -O2: the default build here isn't optimised for speed.
CPYTHON_OPT = "OPT=-DNDEBUG -g -fwrapv -O2 -Wall"

# What each step's result depends on, besides its patch files (STEP_PATCHES):
# a step is redone when any of these change. Bump RECIPE_REVISION when a step
# changes in a way these don't capture.
RECIPE_REVISION = 1
STEP_INPUTS = {
    "vitasdk": (VDPM, VITASDK_CHANNEL),
    "packages": (PACKAGES,),
    "pvr": (PVR_REPO, PVR_STUBS),
    "hostpy": (CYTHON,),
    "libjpeg": (LIBJPEG, LIBJPEG_FLAGS),
    "zlib": (ZLIBNG, ZLIB_FLAGS),
    "sdl2": (SDL2, SDL2_FLAGS),
    "sdl2_image": (SDL2_IMAGE, SDL2_IMAGE_PKGCONFIG, SDL2_IMAGE_FLAGS),
    "harfbuzz": (HARFBUZZ, HARFBUZZ_FLAGS),
    "ffmpeg": (FFMPEG,),
    "cpython": (CPYTHON, CPYTHON_CONFIGURE, CPYTHON_OPT),
}
STEP_PATCHES = {
    "sdl2": ["sdl2-2.32.8-vita-pvr-ime.patch", "sdl2-2.32.8-vita-stdio-buffer.patch"],
    "sdl2_image": ["sdl2_image-2.8.12-jpeg-rgba.patch", "sdl2_image-2.8.12-vita-hw-jpeg.patch"],
    "harfbuzz": ["harfbuzz-hb-ft-static-face-deadlock.patch"],
    "ffmpeg": ["ffmpeg-9.0.2-vita-build.sh"],
    "cpython": ["cpython-vita-timed-waits.patch", "cpython-vita-scandir-d-type.patch"],
}

# Fingerprints the first version of this script (2026-10-30) wrote, each with
# the fingerprint the same recipe has now: a toolchain built by that version
# counts as done while the step's recipe is still that one.
LEGACY_RECIPES = {
    "vitasdk": ("d85b4d0b58b48440f06f13c309f2f8ac472f115082f514ba6b7a3d623bd8cfb0",
                "a395034e9de93eda3d776945705907ccdf7696394c013951cd37cca4cc6b4e65"),
    "packages": ("f4f413cc97bc92b11eb7214f042d68f5103ae77b40954c1fd7933ddac450daba",
                 "477792cedd1c2add290ff4dc6a7221cd4737e4f3eab218c8f3156565fb10b76e"),
    "pvr": ("ab32c913f19abb2b7ecde5a58eb534f7b4953d804226f1652531da0efbaa1986",
            "5e47e7c5dc196b3e024573b71b087fd8276d142614a16e7a0acb0f651df25a31"),
    "hostpy": ("e179a387a43568644f90c57d3f45261dc01718086cd44d70db442664b0070bc3",
               "81ea8035a5b537ce90e7d0f00ddece630bbff4d0735f02617d82cb61e38cd8ab"),
    "libjpeg": ("e52da5e83e30b2495f1d9785f73a02e27c97db52f834d9d0330eebffe33ae8ea",
                "5450d99acd21e6dc3bb9419cbfda43446682084135f70e6af5150d8e17dfb8df"),
    "zlib": ("90141060dbcedc38c7c6ecf3341ca5aec615ac213af4a805dda2382961f63356",
             "968d77ab31aa6222f919d41b4954798e375cc9bdee9cc9362a064bdc9bbd1a6e"),
    "sdl2": ("c8ee8e230eff3d2bf2179903a21cd4f54586a27c6ead528b307db2b462c5653b",
             "879cc81f944e60d8422ecdf1e86ecb925d4f1969c1151a867cdc9e2ac8d2de28"),
    "sdl2_image": ("f54446c6770d1240dc42409c3110c3777a95f2bbfbc11141520982057e6680cd",
                   "41d66293bae398abd6ed0f3c380dea3aaf33ae9813cfa37bc85e4025004bc68c"),
    "harfbuzz": ("82e33a9dfda2b3f230a521bb822b830fe7bd62a8250c7abb42e072942bd6617a",
                 "c35d917e28b1a7046dd13f44174286b2aa8658e48169e696408a79424f3cf48a"),
    "ffmpeg": ("ab0d8164cfd0ec779b8562d9f81628ad0c53af5fd9041991768f7f0c785428a1",
               "dbc1ab733a8b7218f01c48b1c587d2058a7bc10eedad401dadf283b13c781c8e"),
    "cpython": ("7d841caa62b64d6f5c3875feb6e49b40d1fbb1cea52aec4b57c48e00467c73a3",
                "091c32c6cd98376f84cff8e764d27e07895b46cdfba316943a47f507024cd96f"),
}


class StepFailed(Exception):
    pass


class Toolchain:

    def __init__(self, root, jobs):
        self.root = root.resolve()
        self.src = self.root / "src"
        self.downloads = self.root / "downloads"
        self.sdk = self.root / "vitasdk"
        self.jobs = str(jobs)
        self.logs = self.root / "logs"
        self.step = self.log = self.status = None
        self.warnings = [ ]
        self.env = dict(os.environ, VITASDK=str(self.sdk),
                        PATH=f"{self.root / 'hostpy/bin'}:{self.sdk / 'bin'}:{os.environ['PATH']}")

    # Helpers.

    def run(self, cmd, cwd=None, **kwargs):
        """Runs a command with its output in the step's log."""
        line = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
        if self.status:
            self.status.update(f"{self.step}: {line.split()[0].rsplit('/', 1)[-1]}")
        with open(self.log, "a") as log:
            log.write(f"+ {line}" + (f" (in {cwd})" if cwd else "") + "\n")
            log.flush()
            rc = subprocess.run(cmd, cwd=cwd, env=self.env, shell=isinstance(cmd, str),
                                stdout=log, stderr=subprocess.STDOUT, **kwargs).returncode
        if rc:
            raise StepFailed(f"{line} failed ({rc})")

    def fetch(self, url, sha256, name=None):
        path = self.downloads / (name or url.rsplit("/", 1)[1])
        if not path.exists():
            self.downloads.mkdir(parents=True, exist_ok=True)
            if self.status:
                self.status.update(f"{self.step}: downloading {path.name}")
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sha256:
            raise StepFailed(f"{path}: SHA-256 {digest}, expected {sha256} (delete it to download again)")
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
            self.run(["patch", "-Np1", "-i", p], cwd=tree)

    def cmake(self, tree, *args, install=True):
        build = tree / "build-vita"
        self.run(["cmake", "-S", tree, "-B", build, "-G", "Ninja",
                  f"-DCMAKE_TOOLCHAIN_FILE={self.sdk / 'share/vita.toolchain.cmake'}",
                  f"-DCMAKE_INSTALL_PREFIX={self.sdk / 'arm-vita-eabi'}",
                  "-DCMAKE_BUILD_TYPE=Release", *args])
        self.run(["ninja", "-C", build, "-j", self.jobs])
        if install:
            self.run(["ninja", "-C", build, "install"])
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
        self.run(f"yes | vdpm install {' '.join(PACKAGES)}")
        listing = subprocess.run(["vdpm", "list"], env=self.env, capture_output=True, text=True).stdout
        installed = dict(l.split()[:2] for l in listing.splitlines() if len(l.split()) == 2)
        for name, version in PACKAGES.items():
            have = installed.get(name)
            if have is None:
                raise StepFailed(f"vdpm didn't install {name}")
            if have != version:
                self.warnings.append(f"{name} {have} (the eboot was developed with {version})")

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
        tree = self.unpack(self.fetch(*LIBJPEG), "libjpeg-turbo")
        self.cmake(tree, *LIBJPEG_FLAGS)

    def step_zlib(self):
        # Only the library replaces the SDK's; the stock headers stay (same
        # ABI in compat mode).
        tree = self.unpack(self.fetch(*ZLIBNG), "zlib-ng")
        build = self.cmake(tree, *ZLIB_FLAGS, install=False)
        shutil.copy2(build / "libz.a", self.sdk / "arm-vita-eabi/lib/libz.a")

    def step_sdl2(self):
        tree = self.unpack(self.fetch(*SDL2), "SDL2")
        self.patch(tree, PATCHES / "sdl2-2.32.8-vita-pvr-ime.patch", PATCHES / "sdl2-2.32.8-vita-stdio-buffer.patch")
        self.cmake(tree, *SDL2_FLAGS)

    def step_sdl2_image(self):
        tree = self.unpack(self.fetch(*SDL2_IMAGE), "SDL2_image")
        self.patch(tree, self.fetch(*SDL2_IMAGE_PKGCONFIG, name="sdl2_image-pkg-config-fix.patch"),
                   PATCHES / "sdl2_image-2.8.12-jpeg-rgba.patch", PATCHES / "sdl2_image-2.8.12-vita-hw-jpeg.patch")
        self.cmake(tree, *SDL2_IMAGE_FLAGS)

    def step_harfbuzz(self):
        tree = self.unpack(self.fetch(*HARFBUZZ), "harfbuzz")
        self.patch(tree, PATCHES / "harfbuzz-hb-ft-static-face-deadlock.patch")
        self.cmake(tree, *HARFBUZZ_FLAGS)

    def step_ffmpeg(self):
        tree = self.unpack(self.fetch(*FFMPEG), "ffmpeg")
        self.run(["sh", PATCHES / "ffmpeg-9.0.2-vita-build.sh"], cwd=tree)
        self.run(["make", f"-j{self.jobs}"], cwd=tree)
        self.run(["make", "install"], cwd=tree)

    def step_cpython(self):
        tree = self.checkout(*CPYTHON, "cpython-vita")
        self.patch(tree, PATCHES / "cpython-vita-timed-waits.patch", PATCHES / "cpython-vita-scandir-d-type.patch")
        prefix = self.sdk / "arm-vita-eabi"
        self.run(["./configure", f"--prefix={prefix}", *CPYTHON_CONFIGURE], cwd=tree)
        self.run(["make", f"-j{self.jobs}", CPYTHON_OPT], cwd=tree)
        self.run(["make", "install", CPYTHON_OPT], cwd=tree)
        shutil.copy2(tree / "Modules/_decimal/libmpdec/libmpdec.a", prefix / "lib/libmpdec.a")

    STEPS = ["vitasdk", "packages", "pvr", "hostpy", "libjpeg", "zlib", "sdl2", "sdl2_image", "harfbuzz", "ffmpeg", "cpython"]

    def recipe(self, name):
        """A fingerprint of everything the step's result depends on."""
        h = hashlib.sha256(repr((RECIPE_REVISION, STEP_INPUTS[name])).encode())
        for f in STEP_PATCHES.get(name, []):
            h.update((PATCHES / f).read_bytes())
        return h.hexdigest()

    def is_done(self, name):
        marker = self.marker(name)
        if not marker.exists():
            return False
        recorded, recipe = marker.read_text(), self.recipe(name)
        old, then = LEGACY_RECIPES.get(name, (None, None))
        if recorded == old and recipe == then:
            marker.write_text(recipe)
            return True
        return recorded == recipe

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
    tc.logs.mkdir(parents=True, exist_ok=True)

    redo = False
    for name in Toolchain.STEPS:
        if args.only and name != args.only:
            continue
        marker = tc.marker(name)
        recipe = tc.recipe(name)
        forced = name in (args.start, args.only)
        redo = redo or forced or not tc.is_done(name)
        if not redo:
            print(f"  {name}: done earlier")
            continue
        marker.unlink(missing_ok=True)
        tc.step, tc.log = name, tc.logs / f"{name}.log"
        tc.log.write_text("")
        start = time.time()
        try:
            with progress(name) as tc.status:
                getattr(tc, "step_" + name)()
        except (StepFailed, subprocess.CalledProcessError, OSError) as e:
            tail = tc.log.read_text(errors="replace").splitlines()[-30:]
            print("\n".join(tail))
            sys.exit(f"\n{name} failed: {e}\nFull log: {tc.log}")
        finally:
            tc.status = None
        marker.write_text(recipe)
        print(f"  {name}: built in {time.time() - start:.0f} s (log: {tc.log})", flush=True)

    for w in tc.warnings:
        print(f"WARNING: {w}")

    print(f"\nToolchain ready in {tc.root}. Build the eboot with:\n"
          f"  uv run RenPy-Vita-8/pipeline/vita_port_build.py")


if __name__ == "__main__":
    main()
