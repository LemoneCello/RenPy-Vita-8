# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Extract stage: pull the files a Vita build needs out of a game's .rpa.

Always extracts what Ren'Py needs to boot: scripts, fonts, gui/, translations
and files in game/'s root. Optionally adds:

  --include PREFIX      every file under PREFIX (repeatable), e.g. images/01/
  --referenced-by FILE  files a script refers to (repeatable), found with
                        Ren'Py's own naming rules:
                          - show/scene/hide image names -> files in images/
                            named like that (Ren'Py names images after their
                            files: images/01/intro 1.jpg is "intro 1")
                          - play/queue names -> files in audio/ (same rule)
                          - quoted strings that are paths in the archive
                          - for referenced image names, paths in their
                            `image NAME = ...` definitions (e.g. Movie(play=))
  --all                 everything

Always added as well: audio files used through Ren'Py's audio namespace
(`audio.NAME` anywhere in the scripts, e.g. Play("sound", audio.neon) in a
screen). Ren'Py defines audio.NAME for every file under audio/ by its
lower-cased base name, so a file missing from the build turns such a
reference into an AttributeError, not just a missing sound.

Video files go to --videos (for vita_video.py), not into game/. Loose files
next to the archive in the game's directory are copied too.

  uv run vita_extract.py --game MyGame-1.0-pc/game \\
      --include images/01/ --include images/MENUS/ --referenced-by script.rpy
"""

import argparse
import re
import shutil
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

from vita_common import extract_entry, read_rpa_index

VIDEO_EXTS = {".webm", ".mp4", ".ogv", ".mkv", ".avi", ".mpg", ".mpeg", ".m4v"}
SCRIPT_EXTS = {".rpy", ".rpyc", ".rpym", ".rpymc"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg"}
AUDIO_EXTS = {".ogg", ".mp3", ".opus", ".wav", ".flac"}
BOOT_DIRS = {"gui", "tl"}
LOOSE_SKIP = {"cache", "saves", "desktop.ini"}

# show/scene/hide <name...> [at|with|as|behind|onlayer|zorder ...][:]
SHOW_RE = re.compile(r"^\s*(?:show|scene|hide)\s+(?!expression\b|screen\b)([^\n:]+?)\s*(?:\b(?:at|with|as|behind|onlayer|zorder)\b.*)?:?\s*$")
# play/queue <channel> <names or "paths">
PLAY_RE = re.compile(r"^\s*(?:play|queue)\s+\w+\s+(.+?)\s*(?:\b(?:fadein|fadeout|loop|noloop|if_changed|volume)\b.*)?$")
IMAGE_DEF_RE = re.compile(r"^\s*image\s+([^=]+?)\s*=\s*(.+)$")
STRING_RE = re.compile(r"""(?:"([^"\\\n]*)"|'([^'\\\n]*)')""")
WORD_RE = re.compile(r"[A-Za-z_][\w.]*")
# audio.NAME, but not renpy.audio.NAME or some_audio.NAME.
AUDIO_NS_RE = re.compile(r"(?<![\w.])audio\.([A-Za-z_]\w*)")


def boot_file(name):
    p = PurePosixPath(name)
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        return False
    if ext in SCRIPT_EXTS or ext in FONT_EXTS:
        return True
    return len(p.parts) == 1 or p.parts[0] in BOOT_DIRS


def script_text(index, archive, game_dir, name):
    """Source of a .rpy, from the game dir or the archive."""
    loose = game_dir / name
    if loose.exists():
        return loose.read_text(encoding="utf-8", errors="replace")
    off, length, prefix = index[name]
    with open(archive, "rb") as a:
        a.seek(off)
        return (prefix + a.read(length)).decode("utf-8", errors="replace")


def references(texts, index, all_texts):
    """Archive paths referred to by the given script texts."""
    by_lower = {n.lower(): n for n in index}
    images = {}
    audio = {}
    for n in index:
        p = PurePosixPath(n)
        if p.parts[0] == "images" and p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
            images.setdefault(p.stem.lower(), []).append(n)
        elif p.parts[0] == "audio" and p.suffix.lower() in AUDIO_EXTS:
            audio.setdefault(p.stem.lower(), []).append(n)

    def strings(line):
        for m in STRING_RE.finditer(line):
            yield m.group(1) if m.group(1) is not None else m.group(2)

    def as_path(s):
        s = s.strip().replace("\\", "/").lower()
        for cand in (s, "images/" + s, "audio/" + s):
            if cand in by_lower:
                return by_lower[cand]
        return None

    found = set()
    image_names = set()

    for text in texts:
        for line in text.splitlines():
            for s in strings(line):
                path = as_path(s)
                if path:
                    found.add(path)

            m = SHOW_RE.match(line)
            if m:
                words = m.group(1).split()
                # Ren'Py matches the longest defined prefix; try them all.
                for i in range(1, len(words) + 1):
                    name = " ".join(words[:i]).lower()
                    image_names.add(name)
                    found.update(images.get(name, ()))

            m = PLAY_RE.match(line)
            if m:
                for word in WORD_RE.findall(m.group(1)):
                    stem = word.lower().split(".")[-1]
                    found.update(audio.get(stem, ()))

    # `image NAME = ...` definitions (Movie(play=...), Composite, ...) of names
    # used above, from any script.
    for text in all_texts:
        for line in text.splitlines():
            m = IMAGE_DEF_RE.match(line)
            if m and " ".join(m.group(1).split()).lower() in image_names:
                for s in strings(m.group(2)):
                    path = as_path(s)
                    if path:
                        found.add(path)

    return found


def audio_namespace(all_texts, index):
    """Files under audio/ that the scripts use as audio.NAME."""
    audio = {}
    for n in index:
        p = PurePosixPath(n)
        if p.parts[0] == "audio" and p.suffix.lower() in AUDIO_EXTS:
            audio.setdefault(p.stem.lower(), []).append(n)

    found = set()
    for text in all_texts:
        for m in AUDIO_NS_RE.finditer(text):
            found.update(audio.get(m.group(1).lower(), ()))
    return found


def mib(n):
    return f"{n / 2**20:.1f} MiB"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", type=Path, required=True, help="the game's game/ directory")
    ap.add_argument("--include", action="append", default=[], metavar="PREFIX")
    ap.add_argument("--referenced-by", action="append", default=[], metavar="SCRIPT")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("build/extract/game"))
    ap.add_argument("--videos", type=Path, default=Path("build/extract/videos"), help="where video files go")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    archives = sorted(args.game.glob("*.rpa"))
    if len(archives) != 1:
        sys.exit(f"expected one .rpa in {args.game}, found {len(archives)}")
    archive = archives[0]
    index = read_rpa_index(archive)

    selected = {n for n in index if args.all or boot_file(n)}
    reasons = Counter({"boot": len(selected)})

    for prefix in args.include:
        add = {n for n in index if n.lower().startswith(prefix.lower())}
        reasons[f"include {prefix}"] += len(add - selected)
        selected |= add

    rpys = [n for n in index if n.endswith(".rpy")]
    all_texts = [script_text(index, archive, args.game, n) for n in rpys]

    add = audio_namespace(all_texts, index)
    reasons["audio namespace"] += len(add - selected)
    selected |= add

    if args.referenced_by:
        texts = [script_text(index, archive, args.game, n) for n in args.referenced_by]
        add = references(texts, index, all_texts)
        reasons["referenced"] += len(add - selected)
        selected |= add

    videos = {n for n in selected if PurePosixPath(n).suffix.lower() in VIDEO_EXTS}
    game_files = selected - videos

    size = lambda names: sum(index[n][1] + len(index[n][2]) for n in names)
    by_kind = Counter()
    for n in game_files:
        p = PurePosixPath(n)
        by_kind[p.parts[0] if len(p.parts) > 1 else "(root)"] += index[n][1]
    print(f"{len(game_files)} files for game/ ({mib(size(game_files))}), {len(videos)} videos ({mib(size(videos))})")
    for why, n in reasons.items():
        print(f"  {n:6}  {why}")
    for kind, n in by_kind.most_common():
        print(f"  {kind:10} {mib(n)}")
    if args.dry_run:
        return 0

    loose = []
    for path in sorted(args.game.rglob("*")):
        rel = path.relative_to(args.game)
        if not path.is_file() or path.suffix.lower() == ".rpa" or rel.parts[0] in LOOSE_SKIP:
            continue
        loose.append((path, (args.videos if path.suffix.lower() in VIDEO_EXTS else args.out) / rel))

    # Files from an earlier run are kept when they're still wanted and
    # unchanged (same size, written since the archive was), so their
    # modification times stay put and later stages (vita_video.py) don't
    # redo them. Anything no longer selected is removed.
    wanted = {(args.videos if n in videos else args.out) / n for n in selected} | {dest for _path, dest in loose}
    for out in (args.out, args.videos):
        out.mkdir(parents=True, exist_ok=True)
        for p in sorted(out.rglob("*"), reverse=True):
            if p.is_file() and p not in wanted:
                p.unlink()
            elif p.is_dir() and not any(p.iterdir()):
                p.rmdir()

    archive_mtime = Path(archive).stat().st_mtime
    kept = 0
    with open(archive, "rb") as a:
        for i, n in enumerate(sorted(selected), 1):
            off, length, prefix = index[n]
            dest = (args.videos if n in videos else args.out) / n
            try:
                st = dest.stat()
                current = st.st_size == length + len(prefix) and st.st_mtime >= archive_mtime
            except OSError:
                current = False
            if current:
                kept += 1
            else:
                extract_entry(a, off, length, prefix, dest)
            if i % 200 == 0 or i == len(selected):
                print(f"\r  extracted {i}/{len(selected)}", end="", flush=True)
    print(f"\n  {kept} unchanged from an earlier run")

    for path, dest in loose:
        try:
            st, src = dest.stat(), path.stat()
            if st.st_size == src.st_size and st.st_mtime == src.st_mtime:
                continue
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
