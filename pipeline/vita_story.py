# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Story stage: split compiled scripts so story loads on demand on the Vita.

Ren'Py loads every statement of every script at startup; on the Vita that is
~42 s, almost all of it story (label blocks) that isn't needed until the
player gets there. This splits each game .rpyc into:

  game/<file>.rpyc                the boot part: top-level statements Ren'Py
                                  needs at startup (init code, definitions,
                                  screens, images, translations). Same header,
                                  data and source digest as before.
  game/vita_story/<file>.bin      the story part: top-level statements with
                                  nothing boot-relevant inside them, plus
                                  their positions in the original file.
  game/vita_story_index.bin       labels and script files -> story part.

port/vita_boot.py loads a story part the first time a label or statement in
it is looked up, then re-chains the file's statements in their original
order, so fallthrough between labels keeps working.

Boot-relevant: anything with init code (get_init), except user statements
whose registration has no execute_init; early_execute, except plain Python in
the default store; translation blocks, except dialogue in the original
language (Translate with language None, as Ren'Py 6.99 compiled every line). `init` blocks written directly in a
label's block (e.g. `default x = 1` indented under a label) are lifted into
the boot part: they run at init either way, and as story flow they do nothing
(Init.execute only moves on). Files where nothing moves are left out of the
output (the original .rpyc is used).

The splitting loads the compiled scripts as real Ren'Py objects and writes
them back for the Vita's Ren'Py 8, so it runs inside a desktop Ren'Py 8 on
this computer (the port's own Ren'Py is built for the Vita only), which this
script starts itself: the game's own (--renpy, the PC release). A game made
with Ren'Py 7 or older (Python 2) can't be split by its own Ren'Py, so the
official Ren'Py SDK (SDK_VERSION below, the version the port is based on) is
downloaded once (~140 MB, checked against its published SHA-256) into
--cache and used instead; Ren'Py 8 reads older
compiled scripts. --no-sdk skips the split for such games instead (the
scripts then load at startup as usual).

  uv run vita_story.py --renpy MyGame-1.0-pc [--src build/extract/game] [--out build/story/game]
      [--cache build/cache] [--no-sdk]
"""

import argparse
import hashlib
import marshal
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path

from vita_common import download, script_texts

# The official SDK used for games whose own Ren'Py is older than 8: the
# version the port's renpy-8 is based on (keep them in step).
SDK_VERSION = "8.3.3"
SDK_URL = f"https://www.renpy.org/dl/{SDK_VERSION}/renpy-{SDK_VERSION}-sdk.tar.bz2"
SDK_SHA256 = "eab75c79b72bd719719dc0e55cd9e06b0737a235ff87cd6169ed940b1250e7dd"  # from its checksums.txt

INDEX_NAME = "vita_story_index.bin"
STORY_DIR = "vita_story"
FORMAT = 1


def worker(renpy_base, src, out, cds_with_init):
    """Runs under Ren'Py's Python."""
    import types

    sys.path.insert(0, renpy_base)
    import renpy
    renpy.import_all()
    renpy.game.script = types.SimpleNamespace(all_pyexpr=None, record_pycode=False, all_pycode=[])
    renpy.game.log = renpy.python.RollbackLog()
    import renpy.script
    import renpy.ast as ast
    import io
    import renpy.compat.pickle
    from renpy.compat.pickle import dumps

    class Unpickler(renpy.compat.pickle.Unpickler):
        # Classes defined by Ren'Py's or the game's init code (e.g. the parsed
        # layeredimage statements' store.layeredimage.RawLayeredImage) don't
        # exist here, as no init code runs: the split only carries their
        # objects along, so a stand-in that pickles back under the same name
        # will do.
        def find_class(self, module, name):
            try:
                return super().find_class(module, name)
            except (ImportError, AttributeError):
                if module != "store" and not module.startswith("store."):
                    raise
            mod = sys.modules.get(module)
            if mod is None:
                mod = sys.modules[module] = types.ModuleType(module)
            if not hasattr(mod, name):
                setattr(mod, name, type(name, (), { "__module__": module, "__qualname__": name }))
            return getattr(mod, name)

    def loads(s):
        return Unpickler(io.BytesIO(s), fix_imports=True, encoding="utf-8", errors="surrogateescape").load()

    translate_types = tuple(getattr(ast, n) for n in ("Translate", "TranslateBlock", "TranslateEarlyBlock",
                                                      "TranslatePython", "TranslateString") if hasattr(ast, n))

    def boot_relevant(node):
        if isinstance(node, translate_types):
            # A dialogue line in the original language: Ren'Py 6.99 compiled
            # each into a Translate block (language None), Ren'Py 8 into a
            # TranslateSay; the translator takes either when a story part
            # loads. Translations into other languages are needed at boot.
            if type(node) is ast.Translate and node.language is None:
                return False
            return True
        if node.get_init is not None:
            if isinstance(node, ast.UserStatement):
                if node.parsed is not None:
                    name = " ".join(node.parsed[0]) if isinstance(node.parsed[0], (tuple, list)) else node.parsed[0]
                else:
                    # Scripts compiled by older Ren'Py (6.99) store only the
                    # line; Ren'Py parses it later. The name starts it.
                    words = node.line.split()
                    name = next((" ".join(words[:n]) for n in range(len(words), 0, -1)
                                 if " ".join(words[:n]) in cds_with_init), None)
                if name in cds_with_init:
                    return True
            elif node.get_init():
                return True
        if node.early_execute is not None:
            if not (isinstance(node, ast.Python) and node.store == "store"):
                return True
        return False

    index = { "format": FORMAT, "labels": { }, "files": { } }
    total_moved = total_kept = 0

    for rpyc in sorted(Path(src).rglob("*.rpyc")):
        rel = rpyc.relative_to(src).as_posix()
        raw = rpyc.read_bytes()
        digest = raw[-16:]
        # Like Ren'Py's load_file: slot 2 holds the statements after static
        # transforms (e.g. dialogue merged into TranslateSay); without it, read
        # slot 1 and apply them.
        with open(rpyc, "rb") as f:
            bindata = renpy.script.Script.read_rpyc_data(None, f, 2)
        if bindata:
            data, stmts = loads(bindata)
        else:
            with open(rpyc, "rb") as f:
                data, stmts = loads(renpy.script.Script.read_rpyc_data(None, f, 1))
            renpy.translation.restructure(stmts)

        story, boot, lifted = [ ], [ ], [ ]
        for pos, node in enumerate(stmts):
            children = [ ]
            node.get_children(children.append)

            if any(boot_relevant(c) for c in children) and isinstance(node, ast.Label):
                inits = [ c for c in node.block if isinstance(c, ast.Init) ]
                init_nodes = set()
                for i in inits:
                    i.get_children(lambda c: init_nodes.add(id(c)))
                rest = [ c for c in children if id(c) not in init_nodes ]
                if inits and not any(boot_relevant(c) for c in rest):
                    node.block = [ c for c in node.block if not isinstance(c, ast.Init) ]
                    lifted.extend(inits)
                    children = rest

            if any(boot_relevant(c) for c in children):
                boot.append((pos, node))
            else:
                story.append((pos, node, children))

        moved = sum(len(c) for _, _, c in story)
        if not story or moved < 50:
            total_kept += sum(1 for _ in stmts)
            continue

        part = f"{STORY_DIR}/{rel[:-len('.rpyc')]}.bin"
        filename = stmts[0].filename
        payload = {
            "format": FORMAT,
            "filename": filename,
            "total": len(stmts),
            "positions": [ pos for pos, _, _ in story ],
            "boot_names": [ (pos, node.name) for pos, node in boot ],
            "stmts": [ node for _, node, _ in story ],
        }
        (Path(out) / part).parent.mkdir(parents=True, exist_ok=True)
        (Path(out) / part).write_bytes(zlib.compress(dumps(payload), 3))

        boot_rpyc = Path(out) / rel
        boot_rpyc.parent.mkdir(parents=True, exist_ok=True)
        with open(boot_rpyc, "wb") as f:
            # Both slots hold the transformed statements, so Ren'Py reads
            # slot 2 and doesn't transform them again.
            boot_data = dumps((data, [ n for _, n in boot ] + lifted))
            renpy.script.Script.write_rpyc_header(None, f)
            renpy.script.Script.write_rpyc_data(None, f, 1, boot_data)
            renpy.script.Script.write_rpyc_data(None, f, 2, boot_data)
            renpy.script.Script.write_rpyc_md5(None, f, digest)

        index["files"][filename] = part
        for _, _, children in story:
            for c in children:
                if isinstance(c.name, str):
                    index["labels"][c.name] = part

        total_moved += moved
        total_kept += len(boot)
        print(f"  {rel:28} {moved:6} statements to {part}, {len(boot)} top-level kept, {len(lifted)} init blocks lifted")

    with open(Path(out) / INDEX_NAME, "wb") as f:
        marshal.dump(index, f)
    print(f"{total_moved} statements moved to story parts; {len(index['labels'])} labels indexed")


def find_cds_with_init(game_dir):
    """Names of statements the game registers with an execute_init function."""
    names = set()
    pattern = re.compile(r"register_statement\s*\(\s*['\"]([^'\"]+)['\"](.*?)\)\s*$", re.S | re.M)
    for _path, text in script_texts(Path(game_dir)):
        for m in pattern.finditer(text):
            if "execute_init" in m.group(2):
                names.add(m.group(1))
    return names


def host_python(renpy_base):
    """The Ren'Py 8 Python in a desktop build for this computer, or None."""
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform.startswith("linux"):
        candidates = [f"lib/py3-linux-{machine}/python"]
    elif sys.platform == "darwin":
        candidates = ["lib/py3-mac-universal/python", f"lib/py3-mac-{machine}/python"]
    else:
        candidates = [f"lib/py3-windows-{machine}/python.exe"]
    for c in candidates:
        if (renpy_base / c).is_file():
            return renpy_base / c
    return None


def renpy_sdk(cache):
    """The official Ren'Py SDK_VERSION SDK, downloaded and unpacked into cache
    the first time."""
    dest = cache / f"renpy-{SDK_VERSION}-sdk"
    if host_python(dest) is not None and (dest / "renpy").is_dir():
        return dest
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"renpy-{SDK_VERSION}-sdk.tar.bz2"
    if not archive.exists():
        print(f"downloading {SDK_URL}", flush=True)
        download(SDK_URL, archive)
    h = hashlib.sha256()
    with open(archive, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    if h.hexdigest() != SDK_SHA256:
        sys.exit(f"{archive}: SHA-256 {h.hexdigest()}, expected {SDK_SHA256} (delete it to download again)")
    print(f"unpacking {archive.name}", flush=True)
    tmp = cache / f".unpack-renpy-{SDK_VERSION}-sdk"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(tmp, filter="data")
        else:
            # Pythons before 3.11.4: refuse paths and links that leave tmp.
            for m in tar.getmembers():
                targets = [m.name] + ([m.linkname] if m.issym() or m.islnk() else [])
                for target in targets:
                    if target.startswith("/") or ".." in Path(target).parts:
                        sys.exit(f"{archive}: unsafe path {target!r}")
            tar.extractall(tmp)
    (top,) = tmp.iterdir()
    if dest.exists():
        shutil.rmtree(dest)
    top.rename(dest)
    tmp.rmdir()
    if host_python(dest) is None:
        sys.exit(f"the Ren'Py {SDK_VERSION} SDK has no Python for this computer ({sys.platform} {platform.machine()})")
    return dest


def main(argv):
    if argv and argv[0] == "--worker":
        renpy_base, src, out = argv[1:4]
        worker(renpy_base, src, out, set(argv[4:]))
        return 0

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--renpy", type=Path, required=True,
                    help="desktop build of the game (has renpy/ and lib/)")
    ap.add_argument("--src", type=Path, default=Path("build/extract/game"), help="game/ with the .rpyc files")
    ap.add_argument("--out", type=Path, default=Path("build/story/game"))
    ap.add_argument("--cache", type=Path, default=Path("build/cache"),
                    help="where the Ren'Py SDK goes for games made with Ren'Py 7 or older")
    ap.add_argument("--no-sdk", action="store_true",
                    help="for such games, skip the split instead of downloading the SDK")
    args = ap.parse_args(argv)

    if not (args.renpy / "renpy").is_dir():
        sys.exit(f"{args.renpy} isn't a Ren'Py desktop build (no renpy/ folder)")

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    renpy_base = args.renpy
    python = host_python(renpy_base)
    if python is None:
        if args.no_sdk:
            print(f"{args.renpy} has no Ren'Py 8 for this computer: skipping the split (--no-sdk), "
                  "so the scripts load at startup.")
            return 0
        print(f"{args.renpy} has no Ren'Py 8 for this computer: using the Ren'Py {SDK_VERSION} SDK")
        renpy_base = renpy_sdk(args.cache)
        python = host_python(renpy_base)

    cds = find_cds_with_init(args.src)
    if cds:
        print(f"statements with execute_init (kept at boot): {sorted(cds)}")

    cmd = [str(python), os.path.abspath(__file__), "--worker",
           str(renpy_base.resolve()), str(args.src.resolve()), str(args.out.resolve()), *sorted(cds)]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
