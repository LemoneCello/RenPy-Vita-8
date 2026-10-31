# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///
"""Run every stage of a Vita build for one game, from a settings file.

  uv run vita_pipeline.py mygame.toml                 # full game, as a VPK
  uv run vita_pipeline.py mygame.toml --variant slice # [slice] folders only
  uv run vita_pipeline.py mygame.toml --from images --deploy
  uv run vita_pipeline.py mygame.toml --stages build,deploy --dry-run

Without a Vita, the result is a VPK (.../TITLE_ID.vpk) to install however you
like. With --deploy (or deploy named in --stages) the app folder is uploaded
to the Vita over vitacompanion instead, and no VPK is written unless --vpk.
With --usb MOUNT it's copied to the Vita's memory card mounted at MOUNT
(VitaShell's USB mode) instead of over Wi-Fi.

Stages, in order (each is its own script; see README.md):

  extract    vita_extract.py      files from the game's archives
  video      vita_video.py        H.264/AAC re-encodes (incremental)
  images     vita_images.py       half-size images + oversample hook
  fonts      vita_fonts.py        subsets of big fonts
  presplash  vita_presplash_build.py  loading-screen images
  sce_sys    vita_sce_sys.py      param.sfo, icon, LiveArea
  story      vita_story.py        boot/story split of the compiled scripts
  build      vita_build.py        the app folder and its VPK
  deploy     vitactl.py sync      upload to ux0:app/TITLE_ID (only with --deploy
                                  or when named in --stages)

Outputs go to BUILD_ROOT/TITLE_ID/VARIANT/ (default build/games/...), the app
to .../app/TITLE_ID. The eboot comes from [build] port_build, made separately
with vita_port_build.py (it's the same for every game).

Settings file (TOML):

  [game]
  title_id = "ABCD00001"   # required, 9 uppercase letters/digits
  title = "..."            # optional, default: the game's config.name
  dir = "path/game"        # the PC release's game/ directory
  renpy = "path"           # the PC release's base directory (for vita_story)

  [build]
  port_build = "build/port/vita"  # optional; vita_port_build.py's default output
  root = "build/games"     # optional

  [slice]                  # for --variant slice
  include = ["images/01/"]
  referenced_by = ["script.rpy"]

  [stages.NAME]            # extra arguments for a stage
  args = ["--crf", "22"]
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

STAGES = ["extract", "video", "images", "fonts", "presplash", "sce_sys", "story", "build", "deploy"]


def plan(cfg, variant, vpk, usb=None):
    game = cfg["game"]
    build = cfg.get("build", {})
    title_id = game["title_id"]
    root = Path(build.get("root", "build/games")) / title_id / variant
    port_build = build.get("port_build", "build/port/vita")
    extra = {name: list(v.get("args", [])) for name, v in cfg.get("stages", {}).items()}

    d = {
        "extract": root / "extract/game",
        "videos": root / "extract/videos",
        "video": root / "video/game",
        "images": root / "images/game",
        "fonts": root / "fonts/game",
        "presplash": root / "presplash",
        "sce_sys": root / "sce_sys",
        # The story split depends only on the compiled scripts, which every
        # variant extracts in full.
        "story": root.parent / "story/game",
        "app": root / "app",
    }

    if variant == "full":
        selection = ["--all"]
    else:
        sl = cfg.get(variant)
        if sl is None:
            sys.exit(f"No [{variant}] section in the settings file")
        selection = [f"--include={p}" for p in sl.get("include", [])]
        selection += [f"--referenced-by={s}" for s in sl.get("referenced_by", [])]

    sce_sys = ["--game", d["extract"], "--title-id", title_id, "--out", d["sce_sys"]]
    if "title" in game:
        sce_sys += ["--title", game["title"]]

    build_args = ["--title-id", title_id, "--game", d["extract"],
                  "--overlay", d["images"], "--overlay", d["fonts"], "--overlay", d["video"],
                  "--compiled", d["story"], "--port-build", port_build,
                  "--presplash", d["presplash"], "--sce-sys", d["sce_sys"], "--out", d["app"]]
    if vpk:
        build_args += ["--vpk", root / f"{title_id}.vpk"]

    cmds = {
        "extract": ["vita_extract.py", "--game", game["dir"], "--out", d["extract"], "--videos", d["videos"]] + selection,
        "video": ["vita_video.py", "--src", d["videos"], "--dest", d["video"], "--scripts", d["extract"]],
        "images": ["vita_images.py", "--src", d["extract"], "--dest", d["images"]],
        "fonts": ["vita_fonts.py", "--src", d["extract"], "--dest", d["fonts"]],
        "presplash": ["vita_presplash_build.py", d["extract"], d["presplash"]],
        "sce_sys": ["vita_sce_sys.py"] + sce_sys,
        "story": ["vita_story.py", "--renpy", game["renpy"], "--src", d["extract"], "--out", d["story"]],
        "build": ["vita_build.py"] + build_args,
        "deploy": ["vitactl.py", "sync", d["app"] / title_id, f"ux0:app/{title_id}", "--prune"]
                  + (["--usb", usb] if usb else []),
    }

    # The stage scripts are next to this one; they run in the workspace (the
    # current directory), where the settings' relative paths point.
    here = Path(__file__).resolve().parent

    return {name: ["uv", "run", "-q", os.path.relpath(here / cmd[0])] + [str(a) for a in cmd[1:]] + extra.get(name, [])
            for name, cmd in cmds.items()}, title_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("settings", type=Path, help="the game's settings file (TOML)")
    ap.add_argument("--variant", default="full", help="'full' (default) or a section of the settings file, e.g. slice")
    ap.add_argument("--stages", help="comma-separated stages to run (default: all but deploy)")
    ap.add_argument("--from", dest="start", choices=STAGES, help="run from this stage on")
    ap.add_argument("--deploy", action="store_true", help="also upload the app to the Vita (Wi-Fi)")
    ap.add_argument("--usb", metavar="MOUNT",
                    help="deploy by copying to the Vita's memory card mounted at MOUNT (VitaShell's USB mode)")
    ap.add_argument("--vpk", action="store_true", default=None,
                    help="write a VPK (the default unless deploying)")
    ap.add_argument("--no-vpk", dest="vpk", action="store_false", help="don't write a VPK")
    ap.add_argument("--dry-run", action="store_true", help="only print the commands")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the game from the Vita instead of building (asks first); with --usb over USB")
    ap.add_argument("--saves", action="store_true", help="with --uninstall, also remove its Ren'Py saves")
    args = ap.parse_args()

    with open(args.settings, "rb") as f:
        cfg = tomllib.load(f)

    if args.uninstall:
        sys.exit(uninstall(cfg, args))

    if args.stages:
        stages = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = [s for s in stages if s not in STAGES]
        if unknown:
            sys.exit(f"Unknown stages: {', '.join(unknown)} (known: {', '.join(STAGES)})")
    else:
        stages = STAGES[STAGES.index(args.start):] if args.start else list(STAGES)
        if not (args.deploy or args.usb):
            stages.remove("deploy")

    # A VPK unless the app goes to the Vita directly.
    vpk = args.vpk if args.vpk is not None else "deploy" not in stages

    cmds, title_id = plan(cfg, args.variant, vpk, args.usb)

    env = dict(os.environ, VITA_TITLE_ID=title_id)
    con = console()
    times = [ ]

    for name in STAGES:
        if name not in stages:
            continue
        cmd = cmds[name]
        if con:
            con.rule(f"[bold cyan]{name}", align="left")
            con.print(shlex.join(cmd), style="dim", markup=False, highlight=False)
        else:
            print(f"== {name}: {shlex.join(cmd)}", flush=True)
        if args.dry_run:
            continue
        start = time.time()
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            if con:
                con.print(f"[bold red]{name} failed[/] (exit {result.returncode})")
                sys.exit(1)
            sys.exit(f"Stage {name} failed (exit {result.returncode})")
        times.append((name, time.time() - start))
        if not con:
            print(f"== {name} done in {times[-1][1]:.0f} s", flush=True)

    if times and con:
        from rich.table import Table
        table = Table(title=f"{title_id} ({args.variant})", title_justify="left")
        table.add_column("Stage")
        table.add_column("Time", justify="right")
        for name, seconds in times:
            table.add_row(name, f"{seconds:.0f} s")
        table.add_row("[bold]total", f"[bold]{sum(s for _, s in times):.0f} s")
        con.print(table)

    build = cmds["build"]
    if times and "build" in [n for n, _ in times]:
        result = build[build.index("--vpk") + 1] if "--vpk" in build else os.path.join(build[build.index("--out") + 1], title_id)
        print(f"Result: {result}")


# define config.save_directory = "..." in the game's scripts.
SAVE_DIR_RE = re.compile(r"""^\s*define\s+config\.save_directory\s*=\s*(["'])(.+?)\1""", re.M)


def uninstall(cfg, args):
    """Runs vitactl.py uninstall for the game; returns its exit status."""
    title_id = cfg["game"]["title_id"]
    cmd = ["uv", "run", "-q", os.path.relpath(Path(__file__).resolve().parent / "vitactl.py"), "uninstall", title_id]
    if args.saves:
        # The save folder's name, from the extracted scripts (the PC release's
        # archives hold them, so look there after a build).
        extract = Path(cfg.get("build", {}).get("root", "build/games")) / title_id / args.variant / "extract/game"
        names = {m.group(2) for path in sorted(extract.rglob("*.rpy"))
                 for m in SAVE_DIR_RE.finditer(path.read_text(encoding="utf-8", errors="replace"))}
        if len(names) != 1:
            sys.exit(f"Couldn't find the game's config.save_directory in {extract} "
                     f"({'none' if not names else 'several: ' + ', '.join(sorted(names))}); "
                     f"pass it yourself: vitactl.py uninstall {title_id} --saves NAME")
        cmd += ["--saves", names.pop()]
    if args.usb:
        cmd += ["--usb", args.usb]
    print(shlex.join(cmd), flush=True)
    if args.dry_run:
        return 0
    return subprocess.run(cmd).returncode


def console():
    """A rich console for an interactive terminal, else None (plain output)."""
    if not sys.stdout.isatty():
        return None
    try:
        from rich.console import Console
        return Console()
    except ImportError:
        return None


if __name__ == "__main__":
    main()
