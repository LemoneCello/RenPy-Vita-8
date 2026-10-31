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
the default store; translation blocks. `init` blocks written directly in a
label's block (e.g. `default x = 1` indented under a label) are lifted into
the boot part: they run at init either way, and as story flow they do nothing
(Init.execute only moves on). Files where nothing moves are left out of the
output (the original .rpyc is used).

The splitting runs under the game's own desktop Ren'Py Python (it needs
Ren'Py's classes), which this script starts itself. It needs Ren'Py 8
(Python 3): for an older game, --renpy can name any Ren'Py 8 desktop build
(Ren'Py 8 reads older compiled scripts); with only an older Ren'Py, the split
is skipped and the game's scripts load at startup as usual.

  uv run vita_story.py --renpy MyGame-1.0-pc [--src build/extract/game] [--out build/story/game]
"""

import argparse
import marshal
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

from vita_common import script_texts

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
    from renpy.compat.pickle import loads, dumps

    translate_types = tuple(getattr(ast, n) for n in ("Translate", "TranslateBlock", "TranslateEarlyBlock",
                                                      "TranslatePython", "TranslateString") if hasattr(ast, n))

    def boot_relevant(node):
        if isinstance(node, translate_types):
            return True
        if node.get_init is not None:
            if isinstance(node, ast.UserStatement):
                name = " ".join(node.parsed[0]) if isinstance(node.parsed[0], (tuple, list)) else node.parsed[0]
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
    args = ap.parse_args(argv)

    pythons = sorted(args.renpy.glob("lib/py3-*/python"))

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    if not pythons:
        if not (args.renpy / "renpy").is_dir():
            sys.exit(f"{args.renpy} isn't a Ren'Py desktop build (no renpy/ folder)")
        print(f"{args.renpy} has no Ren'Py 8 (Python 3) to split the scripts with: skipping the split, "
              "so the scripts load at startup. Set story_renpy to a Ren'Py 8 desktop build to split them.")
        return 0

    cds = find_cds_with_init(args.src)
    if cds:
        print(f"statements with execute_init (kept at boot): {sorted(cds)}")

    cmd = [str(pythons[0]), os.path.abspath(__file__), "--worker",
           str(args.renpy.resolve()), str(args.src.resolve()), str(args.out.resolve()), *sorted(cds)]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
