# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Generate a Vita app's sce_sys/: param.sfo, the bubble icon and LiveArea art.

The title defaults to the game's config.name (from its .rpy files), the art
to its presplash (presplash_background + presplash_foreground, or
presplash.*), else gui/main_menu.* or gui/game_menu.*, scaled and
centre-cropped. LiveArea only accepts 8-bit palettised PNGs.

  uv run vita_sce_sys.py --game build/extract/game --title-id ABCD00001 --out build/sce_sys

The title ID must be 9 uppercase letters/digits and unique on the Vita; it is
also the app's folder (ux0:app/TITLE_ID). Changing the title of an installed
app only shows after the LiveArea database is refreshed (e.g. VitaShell's
"Refresh LiveArea").
"""

import argparse
import re
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

LIVEAREA_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<livearea style="a1" format-ver="01.00" content-rev="1">
  <livearea-background>
    <image>bg.png</image>
  </livearea-background>
  <gate>
    <startup-image>startup.png</startup-image>
  </gate>
</livearea>
"""

# define config.name = _("My Game") / "My Game" / 'My Game'
NAME_RE = re.compile(r"""^\s*define\s+config\.name\s*=\s*(?:_\(\s*)?(["'])(.*?)\1""", re.M)


# (key, value, max_len); ints are stored as int32, strings as NUL-terminated utf-8.
def sfo_entries(title, stitle, title_id):
    return [
        ("APP_VER", "01.00", 8),
        ("ATTRIBUTE", 0x8000, 4),
        ("ATTRIBUTE2", 12, 4),
        ("ATTRIBUTE_MINOR", 0x10, 4),
        ("BOOT_FILE", "", 32),
        ("CATEGORY", "gd", 4),
        ("CONTENT_ID", "", 48),
        ("EBOOT_APP_MEMSIZE", 0, 4),
        ("EBOOT_ATTRIBUTE", 0, 4),
        ("EBOOT_PHY_MEMSIZE", 0, 4),
        ("LAREA_TYPE", 0, 4),
        ("NP_COMMUNICATION_ID", "", 16),
        ("PARENTAL_LEVEL", 1, 4),
        ("PSP2_DISP_VER", "00.000", 8),
        ("PSP2_SYSTEM_VER", 0, 4),
        ("STITLE", stitle, 52),
        ("TITLE", title, 128),
        ("TITLE_ID", title_id, 12),
        ("VERSION", "00.00", 8),
    ]


def build_sfo(entries):
    entries = sorted(entries, key=lambda e: e[0])
    keys = b""
    data = b""
    index = b""
    for key, value, max_len in entries:
        if isinstance(value, int):
            fmt, payload = 0x0404, struct.pack("<I", value)
        else:
            fmt, payload = 0x0204, value.encode("utf-8") + b"\0"
        if len(payload) > max_len:
            sys.exit(f"param.sfo: {key} value {value!r} longer than {max_len - 1} bytes")
        index += struct.pack("<HHIII", len(keys), fmt, len(payload), max_len, len(data))
        keys += key.encode("ascii") + b"\0"
        data += payload.ljust(max_len, b"\0")

    keys = keys.ljust((len(keys) + 3) & ~3, b"\0")
    key_start = 20 + len(index)
    data_start = key_start + len(keys)
    header = struct.pack("<4sIIII", b"\0PSF", 0x0101, key_start, data_start, len(entries))
    return header + index + keys + data


def parse_sfo(blob):
    magic, _, key_start, data_start, count = struct.unpack_from("<4sIIII", blob)
    if magic != b"\0PSF":
        raise ValueError("bad param.sfo magic")
    out = {}
    for i in range(count):
        key_off, fmt, length, _, data_off = struct.unpack_from("<HHIII", blob, 20 + 16 * i)
        key = blob[key_start + key_off:blob.index(b"\0", key_start + key_off)].decode()
        raw = blob[data_start + data_off:data_start + data_off + length]
        out[key] = struct.unpack("<I", raw)[0] if fmt == 0x0404 else raw.rstrip(b"\0").decode()
    return out


def cover(image, size):
    """Scale and centre-crop image to exactly fill size."""
    w, h = size
    scale = max(w / image.width, h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (resized.width - w) // 2
    top = (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def save_pal8(image, size, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    cover(image, size).convert("RGB").quantize(256).save(dest, optimize=True)


def find_image(game_dir, stem):
    for ext in IMAGE_EXTS:
        p = game_dir / (stem + ext)
        if p.exists():
            return p
    return None


def game_art(game_dir):
    """The game's presplash (background with foreground on top) or menu art."""
    bg = find_image(game_dir, "presplash_background")
    if bg is not None:
        art = Image.open(bg).convert("RGBA")
        fg = find_image(game_dir, "presplash_foreground")
        if fg is not None:
            fg = Image.open(fg).convert("RGBA")
            if fg.size == art.size:
                art = Image.alpha_composite(art, fg)
        return art, bg

    for stem in ("presplash", "gui/main_menu", "gui/game_menu"):
        p = find_image(game_dir, stem)
        if p is not None:
            return Image.open(p).convert("RGBA"), p

    return None, None


def game_name(game_dir):
    for path in sorted(game_dir.rglob("*.rpy")):
        m = NAME_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(2)
    return None


def build_sce_sys(sce_sys, title, stitle, title_id, art):
    if sce_sys.exists():
        shutil.rmtree(sce_sys)
    sce_sys.mkdir(parents=True)

    sfo = build_sfo(sfo_entries(title, stitle, title_id))
    (sce_sys / "param.sfo").write_bytes(sfo)
    parsed = parse_sfo(sfo)
    print(f"  param.sfo: TITLE_ID={parsed['TITLE_ID']} TITLE={parsed['TITLE']!r} STITLE={parsed['STITLE']!r}")

    save_pal8(art, (128, 128), sce_sys / "icon0.png")
    contents = sce_sys / "livearea" / "contents"
    save_pal8(art, (840, 500), contents / "bg.png")
    save_pal8(art, (280, 158), contents / "startup.png")
    (contents / "template.xml").write_text(LIVEAREA_TEMPLATE, encoding="utf-8")


def check_title_id(title_id):
    if len(title_id) != 9 or not title_id.isalnum() or title_id != title_id.upper():
        sys.exit(f"Title ID must be 9 uppercase letters/digits, got {title_id!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", type=Path, default=Path("build/extract/game"), help="extracted game/ directory")
    ap.add_argument("--title-id", required=True, help="9 uppercase letters/digits, e.g. ABCD00001")
    ap.add_argument("--title", help="name under the bubble (default: the game's config.name)")
    ap.add_argument("--stitle", help="short title (default: the title)")
    ap.add_argument("--art", type=Path, help="image for the icon and LiveArea (default: presplash or menu art)")
    ap.add_argument("--out", type=Path, default=Path("build/sce_sys"))
    args = ap.parse_args()

    check_title_id(args.title_id)

    title = args.title or game_name(args.game)
    if not title:
        sys.exit("No config.name found in the game's scripts; pass --title")
    stitle = args.stitle or title
    if len(stitle.encode("utf-8")) > 51:
        stitle = stitle.encode("utf-8")[:51].decode("utf-8", "ignore")

    if args.art:
        art, source = Image.open(args.art).convert("RGBA"), args.art
    else:
        art, source = game_art(args.game)
        if art is None:
            sys.exit("No presplash or menu art found; pass --art")

    print(f"Generating {args.out} (art: {source})")
    build_sce_sys(args.out, title, stitle, args.title_id, art)


if __name__ == "__main__":
    main()
