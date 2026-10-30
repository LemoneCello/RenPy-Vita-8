# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Convert a game's presplash images for port/vita_presplash.py.

Ren'Py's presplash can't run on the Vita port (see port/vita_presplash.py),
and pygame_sdl2 there can't hand image pixels to GL, so the images are
converted here: scaled to fit the Vita screen and stored as raw RGBA, zlib
compressed, with a small header (b"VPS1", width, height as little-endian u32).

Uses game/presplash.* or game/presplash_background.* + presplash_foreground.*,
like Ren'Py. Writes vita_presplash_background.bin (and _foreground.bin) to
OUT_DIR, which goes next to renpy.py in the app folder. Prints nothing and
writes nothing if the game has no presplash.

Images that would have to be enlarged by more than --ai-above (1.25) are
upscaled with an AI model instead of Lanczos (vita_upscale.py, Real-ESRGAN
on the CPU, cached; ~30 s the first time): loading takes a while on the Vita,
and a small presplash looked soft. If that fails (e.g. no network for the
first download), all images fall back to Lanczos, so a background and its
foreground always match. --ai-above 0 turns it off.

  uv run vita_presplash_build.py GAME_DIR OUT_DIR [--size 960x544] [--ai-above 1.25]
"""

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from PIL import Image

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MAGIC = b"VPS1"


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


def convert(src, dst, size, upscaled=None):
    _scale, (w, h) = fit(src, size)
    if upscaled is not None:
        image = upscaled
        how = "ai"
    else:
        image = Image.open(src).convert("RGBA").resize((w, h), Image.LANCZOS)
        how = "lanczos"
    data = MAGIC + struct.pack("<II", w, h) + zlib.compress(image.tobytes(), 6)
    dst.write_bytes(data)
    print(f"{src.name} {Image.open(src).size} -> {dst.name} {w}x{h} ({how}), {len(data) // 1024} KiB")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--size", default="960x544", help="screen size to fit (default 960x544)")
    ap.add_argument("--ai-above", type=float, default=1.25,
                    help="AI-upscale images enlarged by more than this factor (default 1.25; 0: never)")
    args = ap.parse_args(argv)

    size = tuple(int(v) for v in args.size.split("x"))

    single = find(args.game_dir, "presplash")
    background = find(args.game_dir, "presplash_background")
    foreground = find(args.game_dir, "presplash_foreground")

    if single:
        pairs = [(single, "vita_presplash_background.bin")]
    elif background and foreground:
        pairs = [(background, "vita_presplash_background.bin"), (foreground, "vita_presplash_foreground.bin")]
    else:
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
