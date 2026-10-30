# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Port stage: build the RenPy-Vita-8 eboot (and its Python library zip) from source.

Follows the port's own renpy-vita/all_in_one.bash, without touching the source
checkouts: sources are copied into a persistent work folder (only changed files
are rewritten, so later builds recompile only what changed), Cython generates
the C modules, and CMake/Ninja links them with CPython, SDL2 (PVR) and the
other libraries into eboot.bin.

Sources come from git refs or, with `--worktree REPO`, from that repo's
working tree as it is (RenPy-Vita-8's renpy-vita/ and its submodules,
uncommitted changes included). By default renpy-vita/ comes from
RenPy-Vita-8's HEAD and the renpy-8 and pygame_sdl2 submodules from the
commits that HEAD records for them: what a fresh clone of the vita-perf
branch has checked out. `--ref REPO=REF` picks another commit or branch; a
branch that only exists on the remote (a fresh clone has no local vita-perf
branches in its submodules) is taken from origin/.

Run from the workspace: needs the toolchain in build/toolchain/
(vita_toolchain.py; see README.md, "Building the eboot").

  uv run RenPy-Vita-8/pipeline/vita_port_build.py              # into build/port/vita/

Output (build/port/<label>/): eboot.bin, lib/python311.zip, renpy/common/,
renpy.py, the same layout as the port's release zip.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# This script lives in RenPy-Vita-8/pipeline/; the toolchain and outputs are
# in the workspace (the current directory).
PORT = Path(__file__).resolve().parent.parent
WORK = Path.cwd()
TOOLCHAIN = Path(os.environ.get("VITA_TOOLCHAIN") or WORK / "build/toolchain").resolve()  # vita_toolchain.py output
VITASDK = TOOLCHAIN / "vitasdk"
HOSTPY = TOOLCHAIN / "hostpy/bin"

# The port's v0.2.1 release, for building the stock port:
# --ref renpy-8=c28b5086b --ref pygame_sdl2=f271acb --ref renpy-vita=<v0.2.1>
V021_REFS = {"renpy-8": "c28b5086b", "pygame_sdl2": "f271acb"}


def recorded_refs():
    """renpy-vita at RenPy-Vita-8's HEAD, and the submodule commits HEAD records."""
    refs = {"renpy-vita": "HEAD"}
    for name in ("renpy-8", "pygame_sdl2"):
        tree = subprocess.run(["git", "-C", PORT, "ls-tree", "HEAD", name],
                              check=True, capture_output=True, text=True).stdout.split()
        refs[name] = tree[2]
    return refs


def resolve(repo, ref):
    """`ref` as a commit, falling back to origin/`ref` for remote-only branches."""
    for candidate in (ref, f"origin/{ref}"):
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    sys.exit(f"{repo.name}: no commit or branch {ref!r} (try `git -C {repo} fetch`)")

# GCC 14+ turns these old-C warnings into errors; the port's code (and the
# Cython 0.29 output) relies on them, and VitaSDK headers lack a few
# declarations the port uses (sceAvPlayerStreamCount, ...; the stubs have them).
EXTRA_C_FLAGS = ("-Wno-error=implicit-function-declaration -Wno-error=int-conversion "
                 "-Wno-error=incompatible-pointer-types -Wno-error=return-mismatch")

# Appended to the link line: newer libraries need others the port's
# CMakeLists lists before them (LAME 4.0 decodes through mpg123).
EXTRA_LIBS = "-lmpg123"

# Modules the port leaves out (all_in_one.bash: "the naughty/unnecessary ones").
DROPPED_MODULES = ["tinyfiledialogs.c", "_renpytfd.c", "sdl2.c", "pygame_sdl2.mixer.c",
                   "pygame_sdl2.font.c", "pygame_sdl2.mixer_music.c"]


def run(cmd, cwd, env=None, log=None):
    print("+", " ".join(str(c) for c in cmd), f"(in {cwd})", flush=True)
    with open(log, "a") if log else open(os.devnull, "w") as out:
        rc = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env,
                            stdout=out if log else None, stderr=subprocess.STDOUT if log else None).returncode
    if rc:
        sys.exit(f"failed ({rc}): {' '.join(str(c) for c in cmd)}" + (f"; see {log}" if log else ""))


def sync(src, dest, *filters):
    """Copies src/ into dest/ by content, so unchanged files keep their times.

    Changed files get the current time rather than the source's (-a minus
    -t): files from git archive carry the commit's time, so a changed .pyx
    looked older than its generated .c and Cython didn't regenerate it."""
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-rlpD", "--checksum", "--delete", *filters, f"{src}/", f"{dest}/"], check=True)


def export(name, ref, worktree, dest, scratch):
    """The tree of one repo (a git ref, or the working tree) into dest/."""
    repo = PORT if name == "renpy-vita" else PORT / name
    if worktree:
        source = repo / "renpy-vita" if name == "renpy-vita" else repo
    else:
        source = scratch / name
        if source.exists():
            shutil.rmtree(source)
        source.mkdir(parents=True)
        archive = subprocess.run(["git", "-C", repo, "archive", resolve(repo, ref)] + (["renpy-vita"] if name == "renpy-vita" else []),
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "x", "-C", source], input=archive, check=True)
        if name == "renpy-vita":
            source = source / "renpy-vita"
    sync(source, dest, "--exclude=.git", "--exclude=gen3-static", "--exclude=build", "--exclude=*.so",
         "--exclude=source/module", "--exclude=include/module", "--exclude=DistributionTool", "--exclude=tmp_build")


def flatten_into(src, dest, pattern):
    """Every file matching pattern under src/, flattened into dest/ (by content)."""
    for path in src.rglob(pattern):
        target = dest / path.name
        data = path.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="vita", help="names the work and output folders (default: vita)")
    ap.add_argument("--worktree", action="append", default=[], choices=["renpy-8", "pygame_sdl2", "renpy-vita", "all"],
                    help="build this repo from its working tree instead of a git ref (repeatable)")
    ap.add_argument("--ref", action="append", default=[], metavar="REPO=REF",
                    help="git ref for renpy-8, pygame_sdl2 or renpy-vita (default: what RenPy-Vita-8's HEAD records)")
    ap.add_argument("--heap-mb", type=int, default=250, help="newlib heap size (port default 180; see vita_eboot.py)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    refs = recorded_refs()
    for item in args.ref:
        name, _, ref = item.partition("=")
        if name not in refs:
            sys.exit(f"unknown repo {name}")
        refs[name] = ref

    work = TOOLCHAIN / "port-work" / args.label
    out = WORK / "build/port" / args.label
    log = work / "build.log"
    work.mkdir(parents=True, exist_ok=True)
    log.write_text("")

    env = dict(os.environ)
    env["VITASDK"] = str(VITASDK)
    env["PATH"] = f"{HOSTPY}:{VITASDK / 'bin'}:{env['PATH']}"

    for name in ("renpy-8", "pygame_sdl2", "renpy-vita"):
        export(name, refs[name], name in args.worktree or "all" in args.worktree, work / name, work / "export")

    # The heap size lives in main.c.
    main_c = work / "renpy-vita/source/main.c"
    text = main_c.read_text()
    patched = text.replace("_newlib_heap_size_user = 180 * 1024 * 1024", f"_newlib_heap_size_user = {args.heap_mb} * 1024 * 1024")
    if patched == text and args.heap_mb != 180:
        sys.exit("heap size line not found in main.c")
    if patched != text:
        main_c.write_text(patched)

    # Python library zip: the SDK's CPython stdlib plus pygame_sdl2's and
    # Ren'Py's Python files, compiled with -OO into one flat zip.
    tmp = work / "tmp_build"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    shutil.copytree(VITASDK / "arm-vita-eabi/lib/python3.11", tmp, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name, src in (("pygame_sdl2", work / "pygame_sdl2/src/pygame_sdl2"), ("renpy", work / "renpy-8/renpy")):
        shutil.copytree(src, tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
    for path in list(tmp.rglob("*")):
        if path.is_file() and path.suffix != ".py":
            path.unlink()
    # -I: the host Python must not import the Vita's stdlib from the current folder.
    run([HOSTPY / "python3", "-I", "-OO", "-m", "compileall", "-q", "-b", "-j", "0", "."], tmp, env, log)
    for path in list(tmp.rglob("*.py")):
        path.unlink()
    (out / "lib").mkdir(parents=True, exist_ok=True)
    zip_path = out / "lib/python311.zip"
    if zip_path.exists():
        zip_path.unlink()
    run(["7z", "a", "-tzip", "-bd", zip_path, "."], tmp, env, log)
    shutil.rmtree(tmp)

    if (out / "renpy/common").exists():
        shutil.rmtree(out / "renpy/common")
    shutil.copytree(work / "renpy-8/renpy/common", out / "renpy/common")
    shutil.copy2(work / "renpy-8/renpy.py", out / "renpy.py")

    # Cython: generate the C modules (setup.py without a command only
    # cythonizes; distutils then complains about the missing command).
    menv = dict(env, PYGAME_SDL2_STATIC="1")
    subprocess.run([HOSTPY / "python3", "setup.py"], cwd=work / "pygame_sdl2", env=menv,
                   stdout=open(log, "a"), stderr=subprocess.STDOUT)
    renv = dict(env, RENPY_DEPS_INSTALL=str(VITASDK / "arm-vita-eabi"), RENPY_STATIC="1")
    subprocess.run([HOSTPY / "python3", "setup.py"], cwd=work / "renpy-8/module", env=renv,
                   stdout=open(log, "a"), stderr=subprocess.STDOUT)
    if not any((work / "renpy-8/module/gen3-static").glob("*.c")):
        sys.exit(f"Ren'Py's Cython modules weren't generated; see {log}")

    vita = work / "renpy-vita"
    module_src = vita / "source/module"
    module_inc = vita / "include/module"
    module_src.mkdir(parents=True, exist_ok=True)
    (module_inc / "pygame_sdl2/src").mkdir(parents=True, exist_ok=True)
    (module_inc / "libhydrogen").mkdir(parents=True, exist_ok=True)

    # Every .c here comes from renpy-8 and pygame_sdl2 (renpy-vita's own
    # source is main.c, one level up); a file one of them no longer has
    # (e.g. after switching branches) would otherwise stay and be built.
    wanted = {p.name for root in (work / "renpy-8/module", work / "pygame_sdl2") for p in root.rglob("*.c")}
    for stale in module_src.glob("*.c"):
        if stale.name not in wanted:
            stale.unlink()

    flatten_into(work / "renpy-8/module", module_src, "*.c")
    flatten_into(work / "pygame_sdl2", module_src, "*.c")
    for name in DROPPED_MODULES:
        (module_src / name).unlink(missing_ok=True)
    flatten_into(work / "pygame_sdl2", module_inc / "pygame_sdl2", "*.h")
    surface_h = module_inc / "pygame_sdl2/surface.h"
    if surface_h.exists():
        flatten_into(module_inc / "pygame_sdl2", module_inc / "pygame_sdl2/src", "surface.h")
        surface_h.unlink()
    for path in (work / "renpy-8/module").rglob("*.h"):
        target = module_inc / path.relative_to(work / "renpy-8/module")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != path.read_bytes():
            target.write_bytes(path.read_bytes())
    hydrogen = module_src / "hydrogen.c"
    if hydrogen.exists():
        flatten_into(module_src, module_inc / "libhydrogen", "hydrogen.c")
        hydrogen.unlink()

    # vita-elf-create appends the module info (~4.7 KB) to the code segment,
    # and the data segment starts at the next 64 KiB boundary: once the code
    # ended 4,120 bytes short of it, the conversion failed ("segment 1
    # overlaps", 2026-10-30). VitaSDK's linker script keeps __sce_headroom
    # bytes free there when the symbol is defined.
    run(["cmake", "-B", "build", "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_C_FLAGS={EXTRA_C_FLAGS}",
         f"-DCMAKE_C_STANDARD_LIBRARIES={EXTRA_LIBS}",
         "-DCMAKE_EXE_LINKER_FLAGS=-Wl,--defsym,__sce_headroom=0x2000"], vita, env, log)
    run(["ninja", "-C", "build", f"-j{args.jobs}"], vita, env, log)

    shutil.copy2(vita / "build/eboot.bin", out / "eboot.bin")
    shutil.copy2(vita / "build/eboot.elf", out / "eboot.elf")
    print(f"built {out}: " + ", ".join(f"{p.name} {p.stat().st_size // 1024} KiB" for p in (out / "eboot.bin", zip_path)))


if __name__ == "__main__":
    main()
