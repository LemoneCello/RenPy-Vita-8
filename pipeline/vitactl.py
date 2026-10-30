# /// script
# requires-python = ">=3.11"
# dependencies = ["rich"]
# ///
"""Drive the Vita through vitacompanion (commands on :1338, FTP on :1337).

  uv run vitactl.py cmd 'press cross; wait 100ms; release cross'
  uv run vitactl.py launch [TITLEID]        uv run vitactl.py quit [TITLEID|all]
  uv run vitactl.py tap BUTTON [ms]         e.g. tap down, tap cross 150
  uv run vitactl.py shot [OUT_DIR]          screenshot via PS+START, downloaded
  uv run vitactl.py frame [OUT_DIR] [N] [FPS]  grab N frames from the udcd_uvc USB feed
  uv run vitactl.py get REMOTE [LOCAL]      e.g. get ux0:app/ABCD00001/log.txt
  uv run vitactl.py put LOCAL REMOTE        e.g. put renpy.py ux0:app/ABCD00001/renpy.py
  uv run vitactl.py ls REMOTE_DIR
  uv run vitactl.py rm REMOTE [REMOTE...]   delete files (not directories)
  uv run vitactl.py mv REMOTE NEW_REMOTE    rename a file
  uv run vitactl.py boottime [OUT_LOG]       launch, wait for Ren'Py's interface, print the
                                            boot stage times from log.txt
  uv run vitactl.py sync LOCAL_DIR REMOTE_DIR [--dry-run] [--prune]
                                            upload new/changed files, e.g.
                                            sync build/app/ABCD00001 ux0:app/ABCD00001

Set VITA_HOST to override the default address, VITA_VIDEO the capture device.
"""

import ftplib
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from vita_common import progress

HOST = os.environ.get("VITA_HOST", "10.42.0.37")
CMD_PORT, FTP_PORT = 1338, 1337
# The app to drive: VITA_TITLE_ID (vita_pipeline.py sets it), else the first
# line of vita_title_id.txt in the workspace (the current directory).
TITLE_ID = os.environ.get("VITA_TITLE_ID")
if not TITLE_ID and os.path.exists("vita_title_id.txt"):
    with open("vita_title_id.txt") as _f:
        TITLE_ID = _f.readline().strip() or None


def title_id():
    """The app to drive, or exit with a hint if none is set."""
    if not TITLE_ID:
        sys.exit("No app set: set VITA_TITLE_ID or write the title ID into vita_title_id.txt")
    return TITLE_ID
SCREENSHOT_DIR = "ux0:picture/SCREENSHOT"
DEFAULT_SHOT_DIR = Path("build/screenshots")


def cmd(line, timeout=60):
    """Send one command line (may chain with ';') and return the reply text."""
    with socket.create_connection((HOST, CMD_PORT), timeout=10) as s:
        s.settimeout(timeout)
        s.sendall(line.encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while data := s.recv(4096):
            chunks.append(data)
    return b"".join(chunks).decode(errors="replace").strip()


def ftp():
    f = ftplib.FTP()
    f.connect(HOST, FTP_PORT, timeout=30)
    f.login()
    return f


def remote(path):
    # vitacompanion wants absolute device paths like /ux0:/app/...
    return "/" + path.lstrip("/")


def listing(f, path):
    lines = []
    f.retrlines(f"LIST {remote(path)}", lines.append)
    return lines


def get(remote_path, local=None):
    local = Path(local or Path(remote_path).name)
    if local.is_dir():
        local = local / Path(remote_path).name
    local.parent.mkdir(parents=True, exist_ok=True)
    with ftp() as f, open(local, "wb") as out:
        f.retrbinary(f"RETR {remote(remote_path)}", out.write)
    return local


def make_dirs(f, remote_dir):
    """Creates remote_dir and its parents (device paths like ux0:app/X/game)."""
    drive, _, rest = remote_dir.partition(":")
    path = drive + ":"
    for part in [p for p in rest.split("/") if p]:
        path = f"{path}/{part}" if not path.endswith(":") else f"{path}{part}"
        try:
            f.mkd(remote(path))
        except ftplib.error_perm:
            pass


def put(local, remote_path):
    with ftp() as f, open(local, "rb") as src:
        make_dirs(f, remote_path.rsplit("/", 1)[0])
        f.storbinary(f"STOR {remote(remote_path)}", src)


def newest_screenshot(f):
    newest = None
    for line in listing(f, SCREENSHOT_DIR):
        sub = line.split()[-1]
        for entry in listing(f, f"{SCREENSHOT_DIR}/{sub}"):
            name = entry.split()[-1]
            if name.endswith(".jpg") and (newest is None or name > newest[1]):
                newest = (sub, name)
    return newest


def shot(out_dir=DEFAULT_SHOT_DIR):
    with ftp() as f:
        before = newest_screenshot(f)
    cmd("press ps; wait 50ms; press start; wait 300ms; release start; wait 50ms; release ps")
    for _ in range(20):
        time.sleep(0.5)
        with ftp() as f:
            after = newest_screenshot(f)
        if after and after != before:
            sub, name = after
            return get(f"{SCREENSHOT_DIR}/{sub}/{name}", Path(out_dir) / f"vita-{name}")
    sys.exit("no new screenshot appeared (the running app may block screenshots)")


def frame(out_dir=DEFAULT_SHOT_DIR, count="1", fps="1"):
    """Grab frames from the udcd_uvc webcam feed (needs the USB cable and ffmpeg)."""
    device = os.environ.get("VITA_VIDEO", "/dev/video0")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pattern = out_dir / f"uvc-{stamp}-%03d.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2",
                    "-video_size", "960x544", "-i", device,
                    "-vf", f"fps={fps}", "-frames:v", str(count), "-y", str(pattern)],
                   check=True, timeout=30 + int(count) / float(fps))
    return sorted(out_dir.glob(f"uvc-{stamp}-*.png"))


def walk_remote(f, path, rel=""):
    """Yields (relative path, size) for every file under a remote directory."""
    for line in listing(f, path):
        parts = line.split(None, 8)
        if len(parts) < 9 or parts[8] in (".", ".."):
            continue
        name = parts[8]
        child = f"{path}/{name}"
        child_rel = f"{rel}{name}"
        if line.startswith("d"):
            yield from walk_remote(f, child, child_rel + "/")
        else:
            yield child_rel, int(parts[4])


def file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as src:
        while chunk := src.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def sync(local_dir, remote_dir, dry_run=False, prune=False):
    """
    Uploads files under local_dir that are missing on the Vita, differ in size,
    or changed since the last sync (hashes of uploaded files are kept in
    build/deploy/). With prune, deletes files an earlier sync uploaded (they're
    in the manifest) that the build no longer has; anything else that exists
    only on the Vita (logs, saves, compiled scripts, backups) is never touched.
    """
    local_dir = Path(local_dir)
    manifest_path = Path("build/deploy") / (remote_dir.replace(":", "_").replace("/", "_") + ".json")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    local = {p.relative_to(local_dir).as_posix(): p for p in sorted(local_dir.rglob("*")) if p.is_file()}

    with ftp() as f:
        on_vita = dict(walk_remote(f, remote_dir))

    uploads = []
    for rel, path in local.items():
        size = path.stat().st_size
        digest = file_hash(path)
        if rel not in on_vita:
            uploads.append((rel, path, digest, "new"))
        elif on_vita[rel] != size:
            uploads.append((rel, path, digest, "size differs"))
        elif rel in manifest and manifest[rel] != digest:
            uploads.append((rel, path, digest, "changed"))
        else:
            # Same size and nothing recorded otherwise: assume it matches.
            manifest[rel] = digest

    extra = sorted(set(on_vita) - set(local))
    deletes = [rel for rel in extra if prune and rel in manifest]
    kept = [rel for rel in extra if rel not in deletes]
    total = sum(path.stat().st_size for _, path, _, _ in uploads)
    print(f"{len(local)} local files, {len(on_vita)} on the Vita: {len(uploads)} to upload ({total / 2**20:.1f} MiB),"
          f" {len(deletes)} to delete, {len(kept)} only on the Vita (kept)")
    for rel, _, _, why in uploads:
        print(f"  upload {rel} ({why})")
    for rel in deletes:
        print(f"  delete {rel}")
    for rel in kept:
        print(f"  only on Vita: {rel}")

    if dry_run:
        return

    made = set()
    with ftp() as f, progress("Uploading", total=total, unit="bytes") as bar:
        for rel, path, digest, _ in uploads:
            parent = PurePosixPath(rel).parent
            for d in reversed([parent] + list(parent.parents)):
                if str(d) in (".", "") or str(d) in made:
                    continue
                try:
                    f.mkd(remote(f"{remote_dir}/{d}"))
                except ftplib.error_perm:
                    pass
                made.add(str(d))
            with open(path, "rb") as src:
                f.storbinary(f"STOR {remote(f'{remote_dir}/{rel}')}", src,
                             callback=lambda block, rel=rel: bar.advance(len(block), current=rel))
            manifest[rel] = digest
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))

    if deletes:
        with ftp() as f:
            for rel in deletes:
                f.delete(remote(f"{remote_dir}/{rel}"))
                manifest.pop(rel, None)
            print(f"  deleted {len(deletes)} files")
            # Remove folders that became empty, deepest first (fails harmlessly if not).
            dirs = {str(d) for rel in deletes for d in PurePosixPath(rel).parents if str(d) != "."}
            for d in sorted(dirs, key=lambda d: -d.count("/")):
                try:
                    f.rmd(remote(f"{remote_dir}/{d}"))
                except ftplib.error_perm:
                    pass

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))


def boottime(out=None, timeout=300, stop=None):
    """
    Relaunches the game and prints Ren'Py's boot stage timings. log.txt is
    deleted first, so an old copy can't be mistaken for this boot's.

    `stop`, if given, is called at every poll; when it returns true, waiting
    ends early and False is returned (e.g. a crash dump or a test result
    appeared before Ren'Py logged "interface ready"). Otherwise returns True.
    """
    app = f"ux0:app/{title_id()}"
    out = Path(out or "build/logs/boot-log.txt")
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd(f"quit {title_id()}")
    time.sleep(2)
    with ftp() as f:
        try:
            f.delete(remote(f"{app}/log.txt"))
        except ftplib.error_perm:
            pass
    cmd(f"launch {title_id()}")
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(5)
        if stop is not None and stop():
            print(f"stopped waiting for boot after {time.time() - start:.0f} s")
            return False
        try:
            get(f"{app}/log.txt", out)
        except ftplib.error_perm:
            continue
        text = out.read_text(errors="replace")
        if "interface ready" in text:
            break
    else:
        sys.exit(f"no 'interface ready' within {timeout} s; partial log in {out}")

    for line in text.splitlines():
        if " took " in line and not line.startswith(" ") or "interface ready" in line:
            print(line)
    return True


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    op, args = argv[0], argv[1:]
    if op == "cmd":
        print(cmd(" ".join(args)))
    elif op == "launch":
        print(cmd(f"launch {args[0] if args else title_id()}"))
    elif op == "quit":
        print(cmd(f"quit {args[0] if args else title_id()}"))
    elif op == "tap":
        ms = args[1] if len(args) > 1 else "100"
        print(cmd(f"press {args[0]}; wait {ms}ms; release {args[0]}"))
    elif op == "shot":
        print(shot(*args[:1]))
    elif op == "frame":
        print("\n".join(str(p) for p in frame(*args[:3])))
    elif op == "get":
        print(get(*args[:2]))
    elif op == "put":
        put(args[0], args[1])
        print(f"uploaded {args[0]} -> {args[1]}")
    elif op == "rm":
        with ftp() as f:
            for path in args:
                f.delete(remote(path))
                print(f"deleted {path}")
    elif op == "mv":
        with ftp() as f:
            f.rename(remote(args[0]), remote(args[1]))
        print(f"renamed {args[0]} -> {args[1]}")
    elif op == "boottime":
        boottime(*args[:1])
    elif op == "sync":
        flags = {a for a in args if a.startswith("--")}
        paths = [a for a in args if not a.startswith("--")]
        sync(paths[0], paths[1], dry_run="--dry-run" in flags, prune="--prune" in flags)
    elif op == "ls":
        with ftp() as f:
            print("\n".join(listing(f, args[0])))
    else:
        sys.exit(f"unknown command {op!r}; see --help")


if __name__ == "__main__":
    main(sys.argv[1:])
