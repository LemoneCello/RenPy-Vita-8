# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["torch", "torchvision", "spandrel", "pillow", "numpy"]
#
# [[tool.uv.index]]
# name = "pytorch-cpu"
# url = "https://download.pytorch.org/whl/cpu"
# explicit = true
#
# [tool.uv.sources]
# torch = { index = "pytorch-cpu" }
# torchvision = { index = "pytorch-cpu" }
# ///
"""Upscale an image with an AI super-resolution model (Real-ESRGAN), on the CPU.

Used by vita_presplash_build.py for loading-screen images much smaller than
the Vita's screen, where plain scaling looks soft. The model enlarges by its
fixed factor (2 by default); the result is then scaled with Lanczos to
--size if given. RGB is upscaled by the model; an alpha channel, if any, with
Lanczos.

The model weights are downloaded once into build/cache/models/ (checked
against a known SHA-256), and results are cached in build/cache/upscale/ by
the input's content, the model and the target size, so pipeline re-runs cost
nothing. Real-ESRGAN: BSD-3-Clause, https://github.com/xinntao/Real-ESRGAN.

  uv run vita_upscale.py IN OUT [--size WxH] [--model x2plus|x4plus]
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

# Downloads and results are kept in the workspace (the current directory).
ROOT = Path.cwd()
MODELS = {
    "x2plus": ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
               "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb"),
    "x4plus": ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
               "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def model_path(name):
    url, digest = MODELS[name]
    path = ROOT / "build/cache/models" / url.rsplit("/", 1)[1]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url}")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    if digest and sha256(path) != digest:
        sys.exit(f"{path}: unexpected SHA-256 (delete it to download again)")
    return path


def upscale(image, name):
    import numpy as np
    import torch
    from spandrel import ModelLoader

    torch.set_num_threads(max(1, torch.get_num_threads()))
    model = ModelLoader().load_from_file(model_path(name)).eval()

    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    x = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        y = model(x)
    out = (y.squeeze(0).permute(1, 2, 0).clamp(0, 1).numpy() * 255.0).round().astype(np.uint8)

    from PIL import Image
    return Image.fromarray(out, "RGB"), model.scale


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--size", help="final size WxH (Lanczos after the model)")
    ap.add_argument("--model", default="x2plus", choices=sorted(MODELS))
    args = ap.parse_args(argv)

    from PIL import Image

    key = hashlib.sha256(args.src.read_bytes() + f"|{args.model}|{args.size}".encode()).hexdigest()[:32]
    cached = ROOT / "build/cache/upscale" / f"{key}.png"

    if not cached.exists():
        image = Image.open(args.src)
        rgb, scale = upscale(image, args.model)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            alpha = image.convert("RGBA").getchannel("A").resize(rgb.size, Image.LANCZOS)
            rgb.putalpha(alpha)
        result = rgb
        if args.size:
            size = tuple(int(v) for v in args.size.split("x"))
            result = result.resize(size, Image.LANCZOS)
        cached.parent.mkdir(parents=True, exist_ok=True)
        result.save(cached)
        print(f"{args.src.name} {image.size} -> model x{scale} {rgb.size} -> {result.size}")

    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_bytes(cached.read_bytes())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
