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
  uv run vitactl.py sync LOCAL_DIR REMOTE_DIR [--dry-run] [--prune] [--usb MOUNT] [--register]
                                            upload new/changed files, e.g.
                                            sync build/app/ABCD00001 ux0:app/ABCD00001
                                            With --usb, copy them to the memory card
                                            mounted at MOUNT (VitaShell's USB mode)
                                            instead of over Wi-Fi. With --register,
                                            register a new app afterwards (see below).
  uv run vitactl.py register [TITLEID]      give an app copied into ux0:app its home-screen
                                            bubble, as installing a VPK would
  uv run vitactl.py uninstall [TITLEID] [--saves SAVE_DIR] [--usb MOUNT] [--yes]
                                            remove a game (app folder and the system's
                                            leftovers; with --saves also its Ren'Py
                                            saves), after listing it and asking

The Vita's address comes from VITA_HOST, else the first line of vita_host.txt
in the workspace (the current directory). VITA_VIDEO sets the capture device.
"""

import ftplib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from vita_common import progress

CMD_PORT, FTP_PORT = 1338, 1337


def vita_host():
    """The Vita's address: VITA_HOST, else vita_host.txt; exit with a hint if neither is set."""
    host = os.environ.get("VITA_HOST")
    if not host and os.path.exists("vita_host.txt"):
        with open("vita_host.txt") as f:
            host = f.readline().strip()
    if not host:
        sys.exit("The Vita's address isn't set: write it into vita_host.txt in the workspace, or set VITA_HOST.\n"
                 "The Vita shows it under Settings > Network > Wi-Fi Settings, in the connection's advanced\n"
                 "settings. vitacompanion must be running on the Vita (commands on port 1338, FTP on 1337).")
    return host
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
    with socket.create_connection((vita_host(), CMD_PORT), timeout=10) as s:
        s.settimeout(timeout)
        s.sendall(line.encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while data := s.recv(4096):
            chunks.append(data)
    return b"".join(chunks).decode(errors="replace").strip()


def ftp():
    f = ftplib.FTP()
    f.connect(vita_host(), FTP_PORT, timeout=30)
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


class FtpCard:
    """The memory card through vitacompanion's FTP server."""

    def __init__(self, remote_dir):
        self.dir = remote_dir
        self.made = set()

    def __enter__(self):
        self.f = ftp()
        return self

    def __exit__(self, *exc):
        self.f.close()

    def files(self):
        """{relative path: size} of everything under the destination."""
        try:
            return dict(walk_remote(self.f, self.dir))
        except ftplib.error_perm:
            return {}   # the destination doesn't exist yet (a first deploy)

    def makedirs(self, rel_dir):
        device, _, folder = f"{self.dir}/{rel_dir}".partition(":")
        parts = [p for p in folder.split("/") if p not in ("", ".")]
        for i in range(1, len(parts) + 1):
            path = f"{device}:{'/'.join(parts[:i])}"
            if path not in self.made:
                try:
                    self.f.mkd(remote(path))
                except ftplib.error_perm:
                    pass
                self.made.add(path)

    def put(self, rel, path, progress_callback):
        with open(path, "rb") as src:
            self.f.storbinary(f"STOR {remote(f'{self.dir}/{rel}')}", src,
                              callback=lambda block: progress_callback(len(block)))

    def delete(self, rel):
        self.f.delete(remote(f"{self.dir}/{rel}"))

    def rmdir(self, rel_dir):
        try:
            self.f.rmd(remote(f"{self.dir}/{rel_dir}"))
        except ftplib.error_perm:
            pass   # not empty

    def tree(self, rel):
        """(files, bytes) under rel, or None if it doesn't exist."""
        try:
            sizes = [size for _, size in walk_remote(self.f, f"{self.dir}/{rel}")]
        except ftplib.error_perm:
            return None
        return len(sizes), sum(sizes)

    def rmtree(self, rel):
        path = f"{self.dir}/{rel}"
        for line in listing(self.f, path):
            parts = line.split(None, 8)
            if len(parts) < 9 or parts[8] in (".", ".."):
                continue
            if line.startswith("d"):
                self.rmtree(f"{rel}/{parts[8]}")
            else:
                self.f.delete(remote(f"{path}/{parts[8]}"))
        self.f.rmd(remote(path))

    def done(self):
        pass


class MountedCard:
    """The memory card mounted on this computer, e.g. in VitaShell's USB mode."""

    def __init__(self, mount, remote_dir):
        device, _, folder = remote_dir.partition(":")
        if device != "ux0":
            sys.exit(f"--usb works for ux0: only, not {remote_dir}")
        root = Path(mount)
        if not (root / "app").is_dir():
            sys.exit(f"{mount} doesn't look like the Vita's memory card (no app/ folder): in VitaShell's\n"
                     f"USB mode, mount the memory card (ux0:) on this computer and pass its mount point.")
        self.dir = root / folder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def files(self):
        if not self.dir.is_dir():
            return {}
        return {p.relative_to(self.dir).as_posix(): p.stat().st_size for p in self.dir.rglob("*") if p.is_file()}

    def makedirs(self, rel_dir):
        (self.dir / rel_dir).mkdir(parents=True, exist_ok=True)

    # Bytes written between forcing data out to the card: desktops often mount
    # removable drives with write caching, so without this the copy "finishes"
    # at memory speed and the real writing happens, unshown, in the final sync.
    FLUSH_EVERY = 32 << 20

    def put(self, rel, path, progress_callback):
        dest = self.dir / rel
        tmp = dest.with_name(dest.name + ".part")
        pending = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            while block := src.read(1 << 20):
                out.write(block)
                pending += len(block)
                if pending >= self.FLUSH_EVERY:
                    out.flush()
                    os.fsync(out.fileno())
                    progress_callback(pending)
                    pending = 0
            out.flush()
            os.fsync(out.fileno())
            progress_callback(pending)
        tmp.replace(dest)

    def delete(self, rel):
        (self.dir / rel).unlink()

    def rmdir(self, rel_dir):
        try:
            (self.dir / rel_dir).rmdir()
        except OSError:
            pass   # not empty

    def tree(self, rel):
        path = self.dir / rel
        if not path.is_dir():
            return None
        sizes = [p.stat().st_size for p in path.rglob("*") if p.is_file()]
        return len(sizes), sum(sizes)

    def rmtree(self, rel):
        shutil.rmtree(self.dir / rel)

    def done(self):
        # Write everything out before the card is unmounted.
        os.sync()
        print("Done. Eject the memory card on this computer before leaving VitaShell's USB mode.")


def sync(local_dir, remote_dir, dry_run=False, prune=False, usb=None):
    """
    Uploads files under local_dir that are missing on the Vita, differ in size,
    or changed since the last sync (hashes of uploaded files are kept in
    build/deploy/). With prune, deletes files an earlier sync uploaded (they're
    in the manifest) that the build no longer has; anything else that exists
    only on the Vita (logs, saves, compiled scripts, backups) is never touched.

    Over Wi-Fi (vitacompanion's FTP) by default; with usb, the memory card is
    mounted at that path on this computer (VitaShell's USB mode) and files are
    copied there. Both share the record of what's on the card.
    """
    local_dir = Path(local_dir)
    manifest_path = Path("build/deploy") / (remote_dir.replace(":", "_").replace("/", "_") + ".json")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    local = {p.relative_to(local_dir).as_posix(): p for p in sorted(local_dir.rglob("*")) if p.is_file()}

    card = MountedCard(usb, remote_dir) if usb else FtpCard(remote_dir)
    with card:
        on_vita = card.files()

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

    with card, progress("Copying" if usb else "Uploading", total=total, unit="bytes") as bar:
        card.makedirs("")
        for rel, path, digest, _ in uploads:
            card.makedirs(PurePosixPath(rel).parent.as_posix())
            card.put(rel, path, lambda n, rel=rel: bar.advance(n, current=rel))
            manifest[rel] = digest
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))

        for rel in deletes:
            card.delete(rel)
            manifest.pop(rel, None)
        if deletes:
            print(f"  deleted {len(deletes)} files")
            # Remove folders that became empty, deepest first.
            dirs = {str(d) for rel in deletes for d in PurePosixPath(rel).parents if str(d) != "."}
            for d in sorted(dirs, key=lambda d: -d.count("/")):
                card.rmdir(d)

    card.done()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=0, sort_keys=True))


def uninstall(title, saves=None, usb=None, yes=False):
    """
    Removes a game from the Vita: its app folder, the system's leftovers for
    its title ID (LiveArea data, licence, system save folder, cache) and, with
    saves, Ren'Py's save folder (ux0:Ren'Py Data/<config.save_directory>),
    which deleting the app on the Vita leaves behind. Lists what it will
    delete and asks first unless yes. The home-screen bubble can't be removed
    remotely: if the game was installed or registered, delete the bubble on the Vita.
    """
    if not re.fullmatch(r"[A-Z0-9]{9}", title):
        sys.exit(f"{title!r} isn't a title ID (9 capital letters or digits)")
    targets = [f"app/{title}", f"appmeta/{title}", f"license/app/{title}", f"user/00/savedata/{title}",
               f"cache/{title}"]
    if saves:
        if "/" in saves or saves in ("", ".", ".."):
            sys.exit(f"{saves!r} isn't a save folder name")
        targets.append(f"Ren'Py Data/{saves}")

    if not usb:
        try:
            cmd(f"quit {title}")
        except OSError:
            pass   # the Vita is unreachable: the FTP connection below says so

    card = MountedCard(usb, "ux0:") if usb else FtpCard("ux0:")
    with card:
        found = [(t, card.tree(t)) for t in targets]
        found = [(t, n) for t, n in found if n is not None]
        if not found:
            print(f"Nothing of {title} is on the Vita.")
            return
        print(f"To delete from the Vita ({'memory card at ' + usb if usb else 'over Wi-Fi'}):")
        for t, (files, size) in found:
            print(f"  ux0:{t}  ({files} files, {size / 2**20:.1f} MiB)")
        if not yes:
            if not sys.stdin.isatty():
                sys.exit("Not deleting: run in a terminal to confirm, or pass --yes.")
            if input("Delete these? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing deleted.")
                return
        for t, _ in found:
            card.rmtree(t)
            print(f"  deleted ux0:{t}")

    card.done()
    manifest = Path("build/deploy") / f"ux0_app_{title}.json"
    manifest.unlink(missing_ok=True)
    if any(t.startswith("appmeta/") for t, _ in found):
        print("The game was registered: delete its bubble on the home screen too (hold it, then Delete).")


def remote_names(f, path):
    """Names in a remote folder (empty if it doesn't exist)."""
    try:
        return {parts[8] for parts in (line.split(None, 8) for line in listing(f, path)) if len(parts) == 9}
    except ftplib.error_perm:
        return set()


def is_registered(card, title):
    """Whether the system knows the app (installing or registering it makes ux0:appmeta/ID)."""
    return card.tree(f"appmeta/{title}") is not None


def register(title, timeout=120):
    """
    Registers an app folder copied into ux0:app/TITLE with the system, which
    gives it its bubble, as installing a VPK does. The system's promoter only
    takes a folder from elsewhere and moves it into ux0:app itself, so the
    folder is renamed into ux0:temp and handed to vitacompanion's `promote`
    (renames only: seconds, whatever the game's size). The folder needs
    sce_sys/param.sfo (and sce_sys/package/head.bin, which vita_sce_sys.py
    writes; vitacompanion makes one if it's missing).
    """
    if not re.fullmatch(r"[A-Z0-9]{9}", title):
        sys.exit(f"{title!r} isn't a title ID (9 capital letters or digits)")
    app, staging = f"app/{title}", f"temp/vitactl_register_{title}"

    try:
        cmd(f"quit {title}")
    except OSError:
        pass   # the Vita is unreachable: the FTP connection below says so
    with FtpCard("ux0:") as card:
        if is_registered(card, title):
            print(f"{title} is already registered.")
            return
        if "param.sfo" not in remote_names(card.f, f"ux0:{app}/sce_sys"):
            sys.exit(f"ux0:{app}/sce_sys/param.sfo is missing: deploy the app first")
        if staging.split("/")[1] in remote_names(card.f, "ux0:temp"):
            sys.exit(f"ux0:{staging} is left over from an earlier attempt: check it, move it back to\n"
                     f"ux0:{app} or delete it, then try again")
        card.makedirs("temp")
        card.f.rename(remote(f"ux0:{app}"), remote(f"ux0:{staging}"))

    reply = cmd(f"promote ux0:{staging}", timeout=timeout)
    with FtpCard("ux0:") as card:
        deadline = time.time() + timeout
        while not (title in remote_names(card.f, "ux0:app") and is_registered(card, title)):
            if not reply.startswith("Promoted") or time.time() > deadline:
                if (staging.split("/")[1] in remote_names(card.f, "ux0:temp")
                        and title not in remote_names(card.f, "ux0:app")):
                    card.f.rename(remote(f"ux0:{staging}"), remote(f"ux0:{app}"))
                sys.exit(f"Registering {title} failed: {reply or 'no reply'}\n"
                         f"The app folder is back in ux0:{app}.")
            time.sleep(2)
    print(f"Registered {title}: its bubble is on the home screen.")


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
    elif op == "uninstall":
        usb = saves = None
        for opt in ("--usb", "--saves"):
            if opt in args:
                i = args.index(opt)
                value = args[i + 1]
                args = args[:i] + args[i + 2:]
                usb, saves = (value, saves) if opt == "--usb" else (usb, value)
        rest = [a for a in args if not a.startswith("--")]
        uninstall(rest[0] if rest else title_id(), saves=saves, usb=usb, yes="--yes" in args)
    elif op == "sync":
        usb = None
        if "--usb" in args:
            i = args.index("--usb")
            usb = args[i + 1]
            args = args[:i] + args[i + 2:]
        flags = {a for a in args if a.startswith("--")}
        paths = [a for a in args if not a.startswith("--")]
        sync(paths[0], paths[1], dry_run="--dry-run" in flags, prune="--prune" in flags, usb=usb)
        if "--register" in flags and not "--dry-run" in flags:
            title = PurePosixPath(paths[1].partition(":")[2]).name
            if not paths[1].startswith("ux0:app/"):
                sys.exit("--register works for ux0:app/TITLE_ID only")
            if not usb:
                register(title)
            elif not is_registered(MountedCard(usb, "ux0:"), title):
                print(f"{title} isn't registered yet, so it has no bubble. After leaving USB mode,\n"
                      f"register it over Wi-Fi: vitactl.py register {title}")
    elif op == "register":
        register(args[0] if args else title_id())
    elif op == "ls":
        with ftp() as f:
            print("\n".join(listing(f, args[0])))
    else:
        sys.exit(f"unknown command {op!r}; see --help")


if __name__ == "__main__":
    main(sys.argv[1:])
