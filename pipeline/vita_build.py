# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///
"""Build stage: assemble the Vita app folder from the other stages' outputs.

Writes build/app/<TITLE_ID>/, an exact mirror of ux0:app/<TITLE_ID>/ as it
should be on the Vita, and optionally a VPK of it. Later sources win:

  1. the port release zip (eboot.bin, renpy/, lib/, module/, renpy.py)
  2. --eboot          patched eboot.bin (vita_eboot.py), or
     --port-build     an eboot built from source with its Python zip and
                      renpy/ (vita_port_build.py output; --eboot is ignored)
  3. port/            renpy.py with the presplash hook, vita_presplash.py
  4. --presplash      vita_presplash_*.bin (vita_presplash_build.py)
  5. --sce-sys        param.sfo and LiveArea art (vita_sce_sys.py)
  6. --game           the extracted game/ tree (vita_extract.py)
  7. --overlay        game/ overlays, in order (vita_images.py, ...)
  8. --compiled       compiled-script overlay (vita_story.py), .rpyc included
  9. rpy/             compatibility scripts, into game/

Rules applied to game/: Ren'Py's own presplash images are left out (they
crash the port; port/vita_presplash.py draws them from the .bin files
instead), and so are desktop test scripts (zz_*) and cache/saves folders.
Overlays and rpy/ contribute .rpy only: their .rpyc would come from another
Ren'Py version, so the Vita compiles them itself. The game's own .rpyc stay.

Script sources (.rpy, .rpym, in game/ and renpy/common/) whose compiled file
is present and up to date (the MD5 of the source matches the one stored at
the end of the .rpyc) are left out: Ren'Py would otherwise read and hash
every source at each start just to confirm that.

Then game/ assets in subfolders (images/, audio/, gui/, ...) are packed into
RPA archives, one per area (game/vita_images_01.rpa, ...). Ren'Py lists every
loose file under game/ at startup, which on the Vita costs ~20 ms per file
(1,700 files added 35 s to "Early init"); an archive is one index read.
Scripts, videos (AvPlayer opens them by path) and files in game/'s root stay
loose. Archives are byte-for-byte deterministic, so vitactl.py sync only
re-uploads the ones whose contents changed. --no-pack keeps everything loose.

Files are hard-linked from their sources when possible, so the mirror costs
little space. The previous mirror is replaced.

  uv run vita_build.py --title-id ABCD00001 ...
  uv run vita_build.py --title-id ABCD00001 ... --vpk build/MyGame.vpk
"""

import argparse
import hashlib
import os
import pickle
import re
import shutil
import sys
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from vita_common import progress

# The pipeline's own files (port/, rpy/) are next to this script.
HERE = Path(__file__).resolve().parent

# The port's release, whose zip gives the app's base files.
PORT_URL = "https://github.com/Grimiku/RenPy-Vita-8/releases/download/v0.2.1/"

PRESPLASH_STEMS = {"presplash", "presplash_background", "presplash_foreground"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SKIP_GAME_DIRS = {"cache", "saves"}
JUNK_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}

# Never packed: Ren'Py compiles scripts in place, and AvPlayer opens videos by path.
LOOSE_EXTS = {".rpy", ".rpyc", ".rpym", ".rpymc",
              ".webm", ".mp4", ".ogv", ".mkv", ".avi", ".mpg", ".mpeg", ".m4v"}
RPA_KEY = 0x42424242

# Already-compressed formats go into the VPK stored, everything else deflated.
STORED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp3", ".ogg", ".opus", ".zip", ".webm", ".mp4", ".bin"}


def game_rule(rel):
    """Returns why a game/-relative path is left out, or None to keep it."""
    p = PurePosixPath(rel)
    if p.parts[0] in SKIP_GAME_DIRS:
        return "cache/saves"
    if len(p.parts) == 1 and p.stem.lower() in PRESPLASH_STEMS and p.suffix.lower() in IMAGE_EXTS:
        return "presplash image"
    if p.name.startswith("zz_"):
        return "desktop test script"
    if p.name.lower() in JUNK_FILES:
        return "junk"
    return None


class Mirror(object):

    def __init__(self, root):
        self.root = root
        self.sources = { }      # rel -> source label
        self.overridden = [ ]
        self.skipped = [ ]

    def add_file(self, src, rel, label):
        rel = PurePosixPath(rel).as_posix()

        if rel.startswith("game/"):
            why = game_rule(rel[len("game/"):])
            if why:
                self.skipped.append((rel, why))
                return

        dest = self.root / rel
        if rel in self.sources:
            self.overridden.append((rel, self.sources[rel], label))
            dest.unlink()

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
        self.sources[rel] = label

    def add_bytes(self, data, rel, label):
        rel = PurePosixPath(rel).as_posix()
        dest = self.root / rel
        if rel in self.sources:
            self.overridden.append((rel, self.sources[rel], label))
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.sources[rel] = label

    def add_tree(self, src, prefix, label, pattern="*", skip_rpyc=False):
        for path in sorted(src.rglob(pattern)):
            if not path.is_file():
                continue
            rel = f"{prefix}{path.relative_to(src).as_posix()}"
            if skip_rpyc and path.suffix == ".rpyc":
                self.skipped.append((rel, f"compiled script from {label}"))
                continue
            self.add_file(path, rel, label)


def fetch_port_zip(port_zip):
    """Downloads the port's release zip (the PVR build) if it isn't there yet."""
    if port_zip.exists():
        return
    url = PORT_URL + port_zip.name
    print(f"downloading {url}")
    port_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = port_zip.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(port_zip)


def add_port_zip(mirror, port_zip):
    with zipfile.ZipFile(port_zip) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if info.filename.startswith(("game/", "sce_sys/")):
                sys.exit(f"{port_zip.name} unexpectedly contains {info.filename}")
            mirror.add_bytes(z.read(info), info.filename, "port zip")


def check_presplash(mirror, game_dirs, presplash_dir):
    """Warn when the game has presplash images but no converted .bin files."""
    has_images = any(
        (d / f"{stem}{ext}").exists()
        for d in game_dirs for stem in PRESPLASH_STEMS for ext in IMAGE_EXTS)
    has_bins = "vita_presplash_background.bin" in mirror.sources
    if has_images and not has_bins:
        print(f"WARNING: the game has presplash images but {presplash_dir} has no .bin files;"
              " run vita_presplash_build.py, or the Vita shows a black screen while loading.")


def drop_compiled_sources(app_dir, mirror):
    """Leaves out sources whose .rpyc/.rpymc is up to date. Returns the count."""
    dropped = 0
    for rel in sorted(mirror.sources):
        if not rel.endswith((".rpy", ".rpym")) or not rel.startswith(("game/", "renpy/common/")):
            continue
        compiled = rel + "c"
        if compiled not in mirror.sources:
            continue
        source_md5 = hashlib.md5((app_dir / rel).read_bytes()).digest()
        with open(app_dir / compiled, "rb") as f:
            f.seek(-16, 2)
            stored_md5 = f.read(16)
        if source_md5 == stored_md5:
            (app_dir / rel).unlink()
            del mirror.sources[rel]
            dropped += 1
    return dropped


def archive_name(game_rel):
    """The archive a game/-relative file goes into, or None to keep it loose."""
    p = PurePosixPath(game_rel)
    if len(p.parts) < 2 or p.suffix.lower() in LOOSE_EXTS:
        return None
    # Per-subfolder archives where folders are big and change separately
    # (images/01, audio/music, ...), one archive per top folder elsewhere.
    split = p.parts[0].lower() in ("images", "audio") and len(p.parts) >= 3
    area = f"{p.parts[0]}_{p.parts[1]}" if split else p.parts[0]
    return "vita_" + re.sub(r"[^a-z0-9]+", "_", area.lower()).strip("_") + ".rpa"


def write_rpa(path, files, bar=None):
    """Writes an RPA-3.0 archive of (name, source) pairs, deterministically."""
    tmp = path.with_suffix(".part")
    index = { }
    with open(tmp, "wb") as f:
        f.write(b"RPA-3.0 %016x %08x\n" % (0, RPA_KEY))
        for name, src in files:
            data = src.read_bytes()
            index[name] = [(f.tell() ^ RPA_KEY, len(data) ^ RPA_KEY)]
            f.write(data)
            if bar:
                bar.advance(len(data), current=name)
        index_offset = f.tell()
        f.write(zlib.compress(pickle.dumps(index, protocol=2), 9))
        f.seek(0)
        f.write(b"RPA-3.0 %016x %08x\n" % (index_offset, RPA_KEY))
    tmp.replace(path)


def pack(app_dir, mirror):
    groups = { }
    for rel in sorted(mirror.sources):
        if rel.startswith("game/"):
            name = archive_name(rel[len("game/"):])
            if name:
                groups.setdefault(name, [ ]).append(rel)

    total = sum((app_dir / rel).stat().st_size for rels in groups.values() for rel in rels)
    with progress("Packing archives", total=total, unit="bytes") as bar:
        for name, rels in sorted(groups.items()):
            write_rpa(app_dir / "game" / name, [(rel[len("game/"):], app_dir / rel) for rel in rels], bar)
            for rel in rels:
                (app_dir / rel).unlink()
                del mirror.sources[rel]
            mirror.sources["game/" + name] = "packed"
            bar.log(f"  packed {len(rels):5} files into game/{name} ({mib((app_dir / 'game' / name).stat().st_size)})")

    for d in sorted((p for p in (app_dir / "game").rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


def build_vpk(app_dir, vpk_path):
    vpk_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = vpk_path.with_suffix(".part")
    files = [p for p in sorted(app_dir.rglob("*")) if p.is_file()]
    with zipfile.ZipFile(tmp, "w") as vpk, \
            progress("Writing the VPK", total=sum(p.stat().st_size for p in files), unit="bytes") as bar:
        for path in files:
            name = path.relative_to(app_dir).as_posix()
            kind = zipfile.ZIP_STORED if PurePosixPath(name).suffix.lower() in STORED_EXTS else zipfile.ZIP_DEFLATED
            vpk.write(path, name, kind)
            bar.advance(path.stat().st_size, current=name)
    tmp.replace(vpk_path)


def mib(n):
    return f"{n / 2**20:.1f} MiB"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title-id", required=True, help="app folder name (9 uppercase letters/digits)")
    ap.add_argument("--port-zip", type=Path, default=Path("build/cache/RenPy_Vita_8_v0.2.1_PVR.zip"))
    ap.add_argument("--eboot", type=Path, default=Path("build/eboot/eboot_heap250.bin"),
                    help="patched eboot.bin; pass '' to keep the port's")
    ap.add_argument("--port-build", type=Path,
                    help="vita_port_build.py output (eboot.bin, lib/, renpy/, renpy.py) to use over the port zip's")
    ap.add_argument("--port-files", type=Path, default=HERE / "port")
    ap.add_argument("--presplash", type=Path, default=Path("build/presplash/assets"),
                    help="directory with vita_presplash_*.bin")
    ap.add_argument("--sce-sys", type=Path, default=Path("build/sce_sys"))
    ap.add_argument("--game", type=Path, default=Path("build/extract/game"), help="extracted game/ tree")
    ap.add_argument("--overlay", type=Path, action="append", default=None,
                    help="game/ overlay, repeatable, applied in order (default build/images/game)")
    ap.add_argument("--compiled", type=Path, help="compiled-script overlay for game/, .rpyc kept (vita_story.py output)")
    ap.add_argument("--rpy", type=Path, default=HERE / "rpy", help="compatibility scripts for game/")
    ap.add_argument("--out", type=Path, default=Path("build/app"))
    ap.add_argument("--vpk", type=Path, help="also write a VPK here")
    ap.add_argument("--no-pack", action="store_true", help="keep game/ assets as loose files")
    args = ap.parse_args(argv)

    overlays = args.overlay if args.overlay is not None else [Path("build/images/game")]

    fetch_port_zip(args.port_zip)

    for label, path in [("port zip", args.port_zip), ("sce_sys", args.sce_sys), ("game", args.game)] + \
            [("overlay", o) for o in overlays]:
        if not path.exists():
            sys.exit(f"{label} not found: {path}")

    app_dir = args.out / args.title_id
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    mirror = Mirror(app_dir)

    add_port_zip(mirror, args.port_zip)
    if args.port_build:
        if not (args.port_build / "eboot.bin").exists():
            sys.exit(f"no eboot.bin in {args.port_build}")
        label = f"port build {args.port_build.name}"
        mirror.add_file(args.port_build / "eboot.bin", "eboot.bin", label)
        mirror.add_file(args.port_build / "renpy.py", "renpy.py", label)
        mirror.add_tree(args.port_build / "lib", "lib/", label)
        mirror.add_tree(args.port_build / "renpy", "renpy/", label)
    elif str(args.eboot):
        mirror.add_file(args.eboot, "eboot.bin", f"eboot {args.eboot.name}")
    mirror.add_tree(args.port_files, "", "port/", "*.py")
    if args.presplash.is_dir():
        mirror.add_tree(args.presplash, "", "presplash", "vita_presplash_*.bin")
    mirror.add_tree(args.sce_sys, "sce_sys/", "sce_sys")
    mirror.add_tree(args.game, "game/", "game")
    for overlay in overlays:
        mirror.add_tree(overlay, "game/", f"overlay {overlay}", skip_rpyc=True)
    if args.compiled:
        mirror.add_tree(args.compiled, "game/", f"compiled {args.compiled}")
    mirror.add_tree(args.rpy, "game/", "rpy/", "*.rpy")

    check_presplash(mirror, [args.game] + overlays, args.presplash)

    total = sum((app_dir / rel).stat().st_size for rel in mirror.sources)
    print(f"Assembled {app_dir}: {len(mirror.sources)} files, {mib(total)}")

    by_label = { }
    for rel, label in mirror.sources.items():
        by_label[label] = by_label.get(label, 0) + 1
    for label, n in by_label.items():
        print(f"  {n:6}  {label}")

    replaced = [o for o in mirror.overridden if not o[1].startswith("game") or not o[2].startswith("overlay")]
    print(f"  {len(mirror.overridden)} files replaced by a later source"
          f" ({len(mirror.overridden) - len(replaced)} game files by overlays)")
    for rel, old, new in replaced:
        print(f"    {rel}: {old} -> {new}")
    for rel, why in mirror.skipped:
        print(f"  left out {rel} ({why})")

    print(f"  left out {drop_compiled_sources(app_dir, mirror)} script sources with up-to-date compiled files")

    if not args.no_pack:
        pack(app_dir, mirror)
        loose = sum(1 for rel in mirror.sources if rel.startswith("game/"))
        print(f"  {loose} files left loose in game/")

    if args.vpk:
        build_vpk(app_dir, args.vpk)
        print(f"Wrote {args.vpk} ({mib(args.vpk.stat().st_size)})")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
