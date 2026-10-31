# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools"]
# ///
"""Subset big fonts to the characters the game's scripts use.

Games sometimes ship a large font (CJK, a few MB) for a handful of lines.
Ren'Py on the Vita reads a font file into memory when it first uses it
(rpy/vita_font_memory.rpy), and every file costs space on the card. This
writes a copy of each font above --min-mb that keeps only the characters
appearing anywhere in the game's scripts (dialogue, screens, translations;
compiled scripts with no source are read too), plus printable Latin-1 and
general punctuation. Layout
features, kerning and hinting are kept, so the text looks the same.

Text that never appears in a script can lose glyphs in a subset font: names
the player types, or strings built at runtime from data files. That only
matters for a big font used for such text (a CJK game's dialogue font with a
name input, say); check the list this prints. --keep FILE leaves a font out.

  uv run vita_fonts.py --src build/extract/game --dest build/fonts/game
"""

import argparse
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

from vita_common import script_texts

FONT_EXTS = {".ttf", ".otf"}

# Printable ASCII and Latin-1, general punctuation (quotes, dashes, ellipsis).
BASE_CHARS = set(range(0x20, 0x7F)) | set(range(0xA0, 0x100)) | set(range(0x2000, 0x2070))


def script_chars(src):
    chars = set()
    for _path, text in script_texts(src):
        chars.update(ord(c) for c in text)
    return chars


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("build/extract/game"), help="extracted game/ directory")
    ap.add_argument("--dest", type=Path, default=Path("build/fonts/game"), help="overlay output directory")
    ap.add_argument("--min-mb", type=float, default=1.0, help="subset fonts larger than this (default 1 MB)")
    ap.add_argument("--keep", action="append", default=[], metavar="FILE", help="leave this font (path under game/) whole")
    args = ap.parse_args()

    fonts = sorted(p for p in args.src.rglob("*") if p.is_file() and p.suffix.lower() in FONT_EXTS
                   and p.stat().st_size > args.min_mb * 1e6
                   and str(p.relative_to(args.src)) not in args.keep)

    # Remove outputs from earlier runs whose font is no longer subset.
    wanted = {p.relative_to(args.src) for p in fonts}
    if args.dest.exists():
        for old in args.dest.rglob("*"):
            if old.is_file() and old.relative_to(args.dest) not in wanted:
                old.unlink()

    # The build takes this folder as an overlay even when it's empty.
    args.dest.mkdir(parents=True, exist_ok=True)

    if not fonts:
        print("No fonts to subset.")
        return

    chars = script_chars(args.src) | BASE_CHARS
    print(f"{len(chars)} characters used by scripts (with Latin-1 and punctuation)")

    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.legacy_kern = True
    options.hinting = True
    options.notdef_outline = True
    options.glyph_names = False
    options.ignore_missing_unicodes = True

    for path in fonts:
        rel = path.relative_to(args.src)
        out = args.dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)

        # Keep the source's timestamps, so the output only changes with its input.
        font = TTFont(path, lazy=False, recalcTimestamp=False)
        cmap = font.getBestCmap() or {}
        keep = sorted(c for c in chars if c in cmap)

        sub = subset.Subsetter(options)
        sub.populate(unicodes=keep)
        sub.subset(font)
        font.save(out)

        print(f"  {rel}: {len(cmap)} -> {len(keep)} characters, "
              f"{path.stat().st_size / 2**20:.1f} -> {out.stat().st_size / 2**20:.2f} MiB")


if __name__ == "__main__":
    main()
