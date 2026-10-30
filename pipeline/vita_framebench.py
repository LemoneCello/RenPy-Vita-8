# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measure how long the Vita takes to react to input in a Ren'Py game.

Boots the game with Ren'Py's frame profiler on (config.profile, frames over
--threshold-ms, default 100 ms, are logged to log.txt with a stage breakdown), presses through the
splash screens, moves focus on the main menu, starts a new game and advances
dialogue, then splits the log into the menu and dialogue phases and prints the
slow frames per phase with their stages and medians.

  uv run vita_framebench.py --label lazy-movie-channels
  uv run vita_framebench.py --analyze build/profile/frames-LABEL   # re-print

The input sequence defaults to the test game's (6 splash screens; D-pad down
focuses Start); adjust --splash-taps, --menu-moves and --advances for other
games. The profiler script is removed from the Vita afterwards.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

import vitactl

APP = f"ux0:app/{vitactl.title_id()}"
PROFILE_RPY = Path(__file__).resolve().parent / "profile/zz_vita_profile.rpy"
PROFILE_REMOTE = f"{APP}/game/zz_vita_profile.rpy"
PLOG_RPY = Path(__file__).resolve().parent / "profile/zz_vita_plog.rpy"
PLOG_REMOTE = f"{APP}/game/zz_vita_plog.rpy"

# Stage markers in Ren'Py's frame log, in the order they appear in a frame.
MARKERS = [
    ("event", "finish event handling"),
    ("execute", "start of new interaction"),
    ("setup", "start interact_core"),
    ("scene", "computed scene"),
    ("predict", "final predict"),
    # Optional, from profile/zz_vita_plog.rpy.
    ("p.layers+predict1", "zz first predict step"),
    ("p.early", "zz pre per_interact"),
    ("p.per_interact", "zz per_interact visit"),
    ("p.focus", "zz focus"),
    ("p.misc", "zz misc"),
    ("p.audio", "zz audio interact"),
    ("prepare", "start of interact while loop"),
    ("render", "start draw_screen"),
    ("draw", "flip"),
    ("end", "end frame"),
]


def tap(button, ms=40):
    vitactl.cmd(f"press {button}; wait {ms}ms; release {button}")


def snapshot(out):
    vitactl.get(f"{APP}/log.txt", out)
    return out.read_text(errors="replace").splitlines()


def run(args, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / PROFILE_RPY.name
    script.write_text(PROFILE_RPY.read_text().replace("config.profile_time = 0.1", f"config.profile_time = {args.threshold_ms / 1000}"))
    (out_dir / "threshold.txt").write_text(f"{args.threshold_ms}\n")
    vitactl.put(str(script), PROFILE_REMOTE)
    if args.markers:
        vitactl.put(str(PLOG_RPY), PLOG_REMOTE)
    try:
        vitactl.boottime(out_dir / "boot.txt")
        time.sleep(args.settle)
        for _ in range(args.splash_taps):
            tap("cross")
            time.sleep(2)
        time.sleep(3)

        before_menu = snapshot(out_dir / "log.txt")
        for button in args.menu_moves.split():
            tap(button)
            time.sleep(2.5)
        after_menu = snapshot(out_dir / "log.txt")

        tap(args.start)
        time.sleep(8)
        before_dialogue = snapshot(out_dir / "log.txt")
        for _ in range(args.advances):
            tap("cross")
            time.sleep(2.5)
        after_dialogue = snapshot(out_dir / "log.txt")
    finally:
        vitactl.cmd(f"quit {vitactl.title_id()}")
        with vitactl.ftp() as f:
            for path in (PROFILE_REMOTE, PROFILE_REMOTE + "c", PLOG_REMOTE, PLOG_REMOTE + "c"):
                try:
                    f.delete(vitactl.remote(path))
                except Exception:
                    pass

    (out_dir / "menu.txt").write_text("\n".join(after_menu[len(before_menu):]) + "\n")
    (out_dir / "dialogue.txt").write_text("\n".join(after_dialogue[len(before_dialogue):]) + "\n")


def frames(lines):
    cur = [ ]
    for line in lines:
        if not line.strip():
            continue
        cur.append(line)
        if line.rstrip().endswith("end frame"):
            yield cur
            cur = [ ]


def stamp(line):
    try:
        return int(line.split()[0]) / 1000.0
    except (ValueError, IndexError):
        return None


def stages(frame):
    """
    Returns {stage: ms}: the time leading up to each marker, summed over its
    occurrences (an interaction restart repeats markers within a frame).
    """
    by_marker = { marker: name for name, marker in MARKERS }
    rv = { }
    last = 0.0
    for line in frame:
        t = stamp(line)
        if t is None:
            continue
        for marker, name in by_marker.items():
            if line.rstrip().endswith(marker):
                rv[name] = rv.get(name, 0.0) + t - last
                last = t
                break
    rv["total"] = last
    return rv


def analyze(out_dir):
    all_names = [ n for n, _ in MARKERS ] + [ "total" ]
    try:
        threshold = int((out_dir / "threshold.txt").read_text())
    except OSError:
        threshold = 100
    for phase in ("menu", "dialogue"):
        path = out_dir / f"{phase}.txt"
        if not path.exists():
            continue
        rows = [ stages(f) for f in frames(path.read_text(errors="replace").splitlines()) ]
        rows = [ r for r in rows if r.get("total", 0) >= threshold ]
        names = [ n for n in all_names if any(n in r for r in rows) ]
        print(f"\n{phase}: {len(rows)} frames over {threshold} ms, {sum(r['total'] for r in rows) / 1000:.2f} s in all (ms per stage, measured from the previous one)")
        print("  " + " ".join(f"{n[-8:]:>8}" for n in names))
        for r in rows:
            print("  " + " ".join(f"{r[n]:8.0f}" if n in r else f"{'-':>8}" for n in names))
        if rows:
            med = [ statistics.median([ r[n] for r in rows if n in r ]) if any(n in r for r in rows) else None for n in names ]
            print("  " + " ".join(f"{m:8.0f}" if m is not None else f"{'-':>8}" for m in med) + "   median")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--analyze", type=Path, help="only print the results of an earlier run")
    ap.add_argument("--settle", type=float, default=12, help="seconds to wait after the interface is up")
    ap.add_argument("--splash-taps", type=int, default=6)
    ap.add_argument("--menu-moves", default="down right right left left")
    ap.add_argument("--start", default="cross", help="button that starts the game from the last menu focus")
    ap.add_argument("--advances", type=int, default=8)
    ap.add_argument("--threshold-ms", type=int, default=100, help="log frames slower than this (Ren'Py's config.profile_time)")
    ap.add_argument("--markers", action="store_true", help="also log finer stages of interaction setup (zz_vita_plog.rpy)")
    args = ap.parse_args(argv)

    if args.analyze:
        analyze(args.analyze)
        return 0

    out_dir = Path("build/profile") / f"frames-{args.label}"
    run(args, out_dir)
    print(f"logs in {out_dir}")
    analyze(out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
