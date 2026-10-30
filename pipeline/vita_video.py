# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Video stage: re-encode a game's movies for the Vita port.

The port plays movies with Sony's AvPlayer, which needs MP4 (H.264 + AAC);
games usually ship WebM (VP9/VP8 + Opus/Vorbis), which shows nothing. Each
video under SRC becomes DEST/<same path>.mp4, and rpy/vita_video.rpy makes the
port play it when the game asks for the original name.

Default size is 960x540, the Vita's screen: the port's zero-copy video path
(renpy-8 vita-perf, 2026-10-29) plays it at the full frame rate. (480x270 was
the default while every frame was uploaded as a new texture: 960x540 played
at ~7 fps.) The default x264 preset is veryslow: ~10% smaller than slow at
the same CRF, and on the test game's clips 1.7% smaller than placebo at a third of
its encoding time (level 3.1 caps it at 8 reference frames). CRF 20
(2026-10-29) is ~2 dB PSNR better than 23 for ~47% more data. High profile
at CRF 20.2 matches Main at CRF 20 in PSNR and is 3-5% smaller; AvPlayer
plays it (checked on the Vita: every frame on time, picture clean). Since
2026-10-30 the default is CRF 18 (~38% more data than 20.2): a test clip
was compared at 20.2, 18 and with x264's aq-mode 3 on the Vita, and fine
detail (hair) looked noticeably better at 18; aq-mode 3 made no visible
difference, and the blocking left in smooth gradients is in the source.
Needs ffmpeg with libx264.

Videos without sound get a silent AAC track (~1 KB/s): AvPlayer paces a
video-only file at no more than 30 frames per second, so a 60 fps clip
without audio played at half speed (checked on the Vita, 2026-10-30; with
silence added it plays at full speed). Outputs without an audio track are
re-encoded on the next run.

Up-to-date outputs are skipped, so re-runs only encode new or changed videos;
changing --size, --crf, --profile or --preset re-encodes everything (the settings are
kept in DEST/vita_video_settings.json).

Short looping movies can be repeated inside the file (--loop-min-seconds,
off by default since 2026-10-30): AvPlayer loses ~5 frames (~250 ms) each
time it loops a file, so a 1.6 s loop stuttered every 1.6 s; repeated 6
times in one file, it plays without a gap through the repeats and dips once
per 9.6 s (checked on the Vita). The port's own movie player
(renpy-8 module/vita_hwvideo.c) loops without a gap, so this is only worth
turning on (e.g. --loop-min-seconds 8) for a port that still uses AvPlayer. Only files the scripts (--scripts, the game folder with
the .rpy files) use solely as looping Movie images are repeated: every
mention of the file's name must be the play= of a Movie(...) whose loop
isn't False, without mask=/side_mask; a file also played once (a cutscene,
loop=False) keeps its length. Names built at runtime aren't seen, so a file
only ever named that way isn't repeated; one named both ways would be (flag
for other games). The repeat counts are kept in DEST/vita_video_loops.json,
so changing the setting only re-encodes the affected files.

Also writes DEST/vita_video_sizes.json, each movie's original resolution by
the name the game uses (e.g. "images/video/intro.webm": [1920, 1080]): a
Movie without size= is drawn at its file's resolution, so
rpy/vita_video.rpy gives such Movies their original size back.

  uv run vita_video.py [--src build/extract/videos] [--dest build/video/game] [--scripts build/extract/game]
      [--size 960x540] [--crf 18] [--profile high] [--preset veryslow] [--loop-min-seconds 0]
"""

import argparse
import ast
import json
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VIDEO_EXTS = {".webm", ".mp4", ".ogv", ".mkv", ".avi", ".mpg", ".mpeg", ".m4v"}


def has_audio(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                             "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(result.stdout.strip())


def movie_calls(text):
    """Yields the keyword arguments of each Movie(...) call in a script, as
    {name: value} with literal values (non-literal ones as None)."""
    for m in re.finditer(r"\bMovie\s*\(", text):
        depth, i, quote = 0, m.end() - 1, None
        while i < len(text):
            c = text[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        try:
            call = ast.parse("Movie" + text[m.end() - 1:i + 1], mode="eval").body
        except SyntaxError:
            continue
        kwargs = {}
        for kw in call.keywords:
            try:
                kwargs[kw.arg] = ast.literal_eval(kw.value)
            except ValueError:
                kwargs[kw.arg] = None
        yield kwargs


def looping_only(scripts_dir, names):
    """The video names (paths relative to game/) the scripts mention only as
    the play= of looping Movie images (see the module docstring)."""
    texts = [p.read_text(encoding="utf-8", errors="replace")
             for p in sorted(scripts_dir.rglob("*")) if p.suffix in (".rpy", ".rpym")]
    stems = {}
    for n in names:
        stems.setdefault(Path(n).stem.lower(), []).append(n)
    looping = {}
    for text in texts:
        for kw in movie_calls(text):
            play = kw.get("play")
            if not isinstance(play, str):
                continue
            ok = kw.get("loop", True) is not False and not kw.get("mask") and not kw.get("side_mask")
            key = Path(play).stem.lower()
            looping.setdefault(key, []).append(ok)
    rv = set()
    for stem, files in stems.items():
        uses = looping.get(stem, [])
        # Two videos with the same base name can't be told apart: neither.
        if len(files) != 1 or not uses or not all(uses):
            continue
        pattern = re.compile(r"(?<![\w-])" + re.escape(stem) + r"\.\w+", re.IGNORECASE)
        mentions = sum(len(pattern.findall(t)) for t in texts)
        if mentions == len(uses):
            rv.add(files[0])
    return rv


def duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def encode(src, dest, size, crf, preset, profile, repeat=1):
    w, h = size
    tmp = dest.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if repeat > 1:
        # A looping movie, repeated inside the file (see the module docstring).
        cmd += ["-stream_loop", str(repeat - 1)]
    cmd += ["-i", str(src)]
    if has_audio(src):
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        # Silence as long as the video (see the module docstring).
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += [
        # Fit inside WxH keeping the aspect ratio; H.264 needs even sizes.
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-profile:v", profile, "-level", "3.1", "-pix_fmt", "yuv420p",
        "-preset", preset, "-crf", str(crf), "-threads", "2",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        return src, False, result.stderr.strip()[-500:]
    tmp.replace(dest)
    return src, True, ""


def probe_size(src):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                             "-of", "csv=p=0:s=x", str(src)], capture_output=True, text=True)
    try:
        w, h = result.stdout.strip().splitlines()[0].split("x")[:2]
        return [int(w), int(h)]
    except (ValueError, IndexError):
        return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=Path("build/extract/videos"))
    ap.add_argument("--dest", type=Path, default=Path("build/video/game"))
    ap.add_argument("--size", default="960x540", help="fit videos inside WxH (default 960x540)")
    ap.add_argument("--preset", default="veryslow", help="x264 preset (default veryslow)")
    ap.add_argument("--crf", type=float, default=18.0, help="x264 quality, lower is better (default 18)")
    ap.add_argument("--profile", default="high", help="H.264 profile (default high)")
    ap.add_argument("--scripts", type=Path, help="game folder with the .rpy files, to find looping movies")
    ap.add_argument("--loop-min-seconds", type=float, default=0.0,
                    help="repeat looping movies shorter than this inside the file (default 0: off)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")
    size = tuple(int(v) for v in args.size.split("x"))

    settings = {"size": list(size), "crf": args.crf, "preset": args.preset, "profile": args.profile}
    settings_path = args.dest / "vita_video_settings.json"
    try:
        changed = json.loads(settings_path.read_text()) != settings
    except (OSError, ValueError):
        changed = True

    candidates = []
    expected = set()
    for src in sorted(args.src.rglob("*")):
        if not src.is_file() or src.suffix.lower() not in VIDEO_EXTS:
            continue
        dest = args.dest / src.relative_to(args.src).with_suffix(".mp4")
        expected.add(dest)
        candidates.append((src, dest))

    # How many times each video is repeated in its file (looping movies).
    loops_path = args.dest / "vita_video_loops.json"
    try:
        old_repeats = json.loads(loops_path.read_text())
    except (OSError, ValueError):
        old_repeats = {}
    repeats = {}
    if args.scripts and args.loop_min_seconds > 0:
        by_name = {src.relative_to(args.src).as_posix(): src for src, _dest in candidates}
        loop_names = sorted(looping_only(args.scripts, by_name))
        with ThreadPoolExecutor(max(4, args.jobs)) as pool:
            for n, d in zip(loop_names, pool.map(duration, [by_name[n] for n in loop_names])):
                if 0 < d < args.loop_min_seconds:
                    repeats[n] = math.ceil(args.loop_min_seconds / d)
        print(f"{len(loop_names)} videos only used as looping movies, {len(repeats)} repeated "
              f"to at least {args.loop_min_seconds:g} s")

    def repeat_of(src):
        return repeats.get(src.relative_to(args.src).as_posix(), 1)

    def needs_encode(pair):
        src, dest = pair
        if changed or not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
            return True
        if old_repeats.get(src.relative_to(args.src).as_posix(), 1) != repeat_of(src):
            return True
        # Encodes from before silent tracks were added.
        return not has_audio(dest)

    with ThreadPoolExecutor(max(4, args.jobs)) as pool:
        jobs = [pair for pair, need in zip(candidates, pool.map(needs_encode, candidates)) if need]
    for _src, dest in jobs:
        dest.parent.mkdir(parents=True, exist_ok=True)

    # Outputs whose source is gone (e.g. a smaller extract) would still ship.
    stale = [p for p in args.dest.rglob("*.mp4") if p not in expected] if args.dest.exists() else []
    for p in stale:
        p.unlink()

    print(f"{len(expected)} videos, {len(jobs)} to encode at {size[0]}x{size[1]}, {len(stale)} stale removed")
    failed = 0
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(encode, src, dest, size, args.crf, args.preset, args.profile, repeat_of(src))
                   for src, dest in jobs]
        for i, fut in enumerate(futures, 1):
            src, ok, err = fut.result()
            name = src.relative_to(args.src).as_posix()
            if ok:
                old_repeats[name] = repeat_of(src)
            else:
                failed += 1
                old_repeats.pop(name, None)
                print(f"\n  FAILED {src}: {err}")
            print(f"\r  encoded {i}/{len(jobs)}", end="", flush=True)
    if jobs:
        print()
    names_now = {src.relative_to(args.src).as_posix() for src, _dest in candidates}
    recorded = {n: k for n, k in sorted(old_repeats.items()) if n in names_now and k != 1}
    args.dest.mkdir(parents=True, exist_ok=True)
    loops_path.write_text("{\n" + ",\n".join(f"  {json.dumps(n)}: {k}" for n, k in recorded.items()) + "\n}\n")
    if not failed:
        args.dest.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings) + "\n")

    sizes_path = args.dest / "vita_video_sizes.json"
    try:
        sizes = json.loads(sizes_path.read_text())
    except (OSError, ValueError):
        sizes = {}
    names = {}
    for src in sorted(args.src.rglob("*")):
        if src.is_file() and src.suffix.lower() in VIDEO_EXTS:
            names[src.relative_to(args.src).as_posix()] = src
    with ThreadPoolExecutor(args.jobs) as pool:
        missing = [n for n in names if n not in sizes]
        for n, size_ in zip(missing, pool.map(probe_size, [names[n] for n in missing])):
            if size_:
                sizes[n] = size_
    sizes = {n: sizes[n] for n in sorted(names) if n in sizes}
    args.dest.mkdir(parents=True, exist_ok=True)
    sizes_path.write_text("{\n" + ",\n".join(f"  {json.dumps(n)}: {json.dumps(v)}" for n, v in sizes.items()) + "\n}\n")

    before = sum(p.stat().st_size for p in args.src.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    after = sum(p.stat().st_size for p in expected if p.exists())
    print(f"{before / 2**20:.1f} MiB -> {after / 2**20:.1f} MiB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
