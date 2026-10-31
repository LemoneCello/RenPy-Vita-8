# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Convert a game's presplash images for port/vita_presplash.py.

Ren'Py's presplash can't run on the Vita port (see port/vita_presplash.py),
and pygame_sdl2 there can't hand image pixels to GL, so the images are
converted here: scaled to fit the Vita screen and stored as raw RGBA, zlib
compressed, with a small header (b"VPS1", width, height as little-endian u32).

Uses, in order:
  1. --background IMG --foreground IMG: a pair chosen in the settings
     (presplash_background, presplash_foreground), the foreground revealed
     left to right as loading goes on, as in Ren'Py.
  2. game/presplash.* or game/presplash_background.* + presplash_foreground.*,
     like Ren'Py.
  3. For a game without a presplash: --art IMG (the LiveArea background art
     from the settings), else the game's gui/main_menu or gui/game_menu art,
     made into a pair: the art filling the screen with an empty progress bar
     near the bottom, and the same with the bar filled.
Writes vita_presplash_background.bin (and _foreground.bin) to OUT_DIR, which
goes next to renpy.py in the app folder. Writes nothing if none of these
exists.

Images that would have to be enlarged by more than --ai-above (1.25) are
upscaled with an AI model instead of Lanczos (vita_upscale.py, Real-ESRGAN
on the CPU, cached; ~30 s the first time): loading takes a while on the Vita,
and a small presplash looked soft. If that fails (e.g. no network for the
first download), all images fall back to Lanczos, so a background and its
foreground always match. --ai-above 0 turns it off.

  uv run vita_presplash_build.py GAME_DIR OUT_DIR [--size 960x544] [--ai-above 1.25]
      [--background IMG --foreground IMG] [--art IMG]
"""

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MAGIC = b"VPS1"

# The progress bar drawn on art made into a presplash, in pixels of the
# 960x544 screen. It spans nearly the full width because the loading screen
# reveals the foreground's left part across the whole image: a short bar
# would sit still at first and fill up early.
BAR_MARGIN = 24        # from the left and right edges
BAR_BOTTOM = 22        # from the bottom edge to the bar
BAR_HEIGHT = 6
BAR_BORDER = 3         # dark track around the bar, visible on light art
TRACK = (0, 0, 0, 150)
FILL = (255, 255, 255, 235)


def find(game_dir, base):
    for ext in EXTENSIONS:
        p = game_dir / (base + ext)
        if p.exists():
            return p
    return None


def fit(src, size):
    """The scale factor and size that fit src inside size."""
    with Image.open(src) as image:
        scale = min(size[0] / image.width, size[1] / image.height)
        return scale, (max(1, int(image.width * scale)), max(1, int(image.height * scale)))


def ai_upscale(sources, size):
    """AI-upscaled copies of sources (vita_upscale.py), or None if any fails."""
    uv = shutil.which("uv")
    if not uv:
        print("ai upscale: uv not found, using Lanczos")
        return None
    tmp = Path(tempfile.mkdtemp(prefix="vita_presplash_"))
    script = Path(__file__).resolve().with_name("vita_upscale.py")
    images = []
    for i, src in enumerate(sources):
        _scale, (w, h) = fit(src, size)
        out = tmp / f"{i}.png"
        result = subprocess.run([uv, "run", "--quiet", str(script), str(src), str(out), "--size", f"{w}x{h}"])
        if result.returncode != 0 or not out.exists():
            print(f"ai upscale of {src.name} failed, using Lanczos")
            return None
        images.append(Image.open(out).convert("RGBA"))
    return images


def write_bin(image, dst):
    data = MAGIC + struct.pack("<II", image.width, image.height) + zlib.compress(image.tobytes(), 6)
    dst.write_bytes(data)
    return len(data)


def convert(src, dst, size, upscaled=None):
    _scale, (w, h) = fit(src, size)
    if upscaled is not None:
        image = upscaled
        how = "ai"
    else:
        image = Image.open(src).convert("RGBA").resize((w, h), Image.LANCZOS)
        how = "lanczos"
    n = write_bin(image, dst)
    print(f"{src.name} {Image.open(src).size} -> {dst.name} {w}x{h} ({how}), {n // 1024} KiB")


def cover(src, size):
    """src scaled to cover size, centre-cropped to it."""
    image = Image.open(src).convert("RGBA")
    scale = max(size[0] / image.width, size[1] / image.height)
    w, h = max(size[0], round(image.width * scale)), max(size[1], round(image.height * scale))
    image = image.resize((w, h), Image.LANCZOS)
    left, top = (w - size[0]) // 2, (h - size[1]) // 2
    return image.crop((left, top, left + size[0], top + size[1]))


def with_bar(image, filled):
    """image with the progress bar's track, and its fill if filled."""
    s = image.width / 960
    x0, x1 = round(BAR_MARGIN * s), image.width - round(BAR_MARGIN * s)
    y1 = image.height - round(BAR_BOTTOM * s)
    y0 = y1 - max(2, round(BAR_HEIGHT * s))
    b = max(1, round(BAR_BORDER * s))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((x0 - b, y0 - b, x1 + b, y1 + b), radius=(y1 - y0 + 2 * b) // 2, fill=TRACK)
    if filled:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=FILL)
    return Image.alpha_composite(image, overlay)


def from_art(art, out_dir, size):
    """A background/foreground pair from one picture, with a progress bar."""
    image = cover(art, size)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filled, name in ((False, "vita_presplash_background.bin"), (True, "vita_presplash_foreground.bin")):
        n = write_bin(with_bar(image, filled), out_dir / name)
        print(f"{art.name} -> {name} {size[0]}x{size[1]} with a progress bar, {n // 1024} KiB")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--size", default="960x544", help="screen size to fit (default 960x544)")
    ap.add_argument("--ai-above", type=float, default=1.25,
                    help="AI-upscale images enlarged by more than this factor (default 1.25; 0: never)")
    ap.add_argument("--background", type=Path, help="presplash background to use instead of the game's")
    ap.add_argument("--foreground", type=Path, help="its foreground (revealed as loading goes on)")
    ap.add_argument("--art", type=Path,
                    help="for a game without a presplash: a picture to show with a progress bar")
    args = ap.parse_args(argv)
    if (args.background is None) != (args.foreground is None):
        sys.exit("--background and --foreground go together")
    for path in (args.background, args.foreground, args.art):
        if path is not None and not path.is_file():
            sys.exit(f"{path} not found")

    size = tuple(int(v) for v in args.size.split("x"))

    # Outputs of an earlier run (a stale foreground would turn a single
    # picture into a progress bar).
    for name in ("vita_presplash_background.bin", "vita_presplash_foreground.bin"):
        (args.out_dir / name).unlink(missing_ok=True)

    single = find(args.game_dir, "presplash")
    background = find(args.game_dir, "presplash_background")
    foreground = find(args.game_dir, "presplash_foreground")

    if args.background:
        pairs = [(args.background, "vita_presplash_background.bin"), (args.foreground, "vita_presplash_foreground.bin")]
    elif single:
        pairs = [(single, "vita_presplash_background.bin")]
    elif background and foreground:
        pairs = [(background, "vita_presplash_background.bin"), (foreground, "vita_presplash_foreground.bin")]
    else:
        art = args.art or find(args.game_dir, "gui/main_menu") or find(args.game_dir, "gui/game_menu")
        if art is None:
            print("No presplash or art to make one from; the Vita shows a black screen while loading.")
            return 0
        from_art(art, args.out_dir, size)
        return 0

    upscaled = None
    if args.ai_above > 0 and any(fit(src, size)[0] > args.ai_above for src, _name in pairs):
        upscaled = ai_upscale([src for src, _name in pairs], size)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for i, (src, name) in enumerate(pairs):
        convert(src, args.out_dir / name, size, upscaled[i] if upscaled else None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
