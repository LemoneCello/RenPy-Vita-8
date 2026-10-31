"""Shared helpers for the stage scripts (imported, not run)."""

import io
import pickle
import sys
import time
import urllib.error
import urllib.request
import zlib

CHUNK = 1 << 20


# --- Downloads ------------------------------------------------------------

def download(url, path, attempts=4):
    """Downloads URL to PATH (via PATH.part, so a broken download never looks
    finished), retrying after connection resets and timeouts. HTTP errors
    such as 404 fail at once."""
    tmp = path.with_suffix(".part")
    for attempt in range(1, attempts + 1):
        try:
            urllib.request.urlretrieve(url, tmp)
            break
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError) as e:
            if attempt == attempts:
                raise
            delay = 5 * attempt
            print(f"  download of {url} failed ({e}); retrying in {delay} s", file=sys.stderr, flush=True)
            time.sleep(delay)
    tmp.replace(path)


# --- RPA reading ----------------------------------------------------------

class _IndexUnpickler(pickle.Unpickler):
    """The archive is untrusted data: allow only what an RPA index needs."""

    ALLOWED = {
        ("_codecs", "encode"),
        ("builtins", "bytes"),
        ("builtins", "str"),
        ("builtins", "tuple"),
        ("builtins", "list"),
        ("builtins", "dict"),
    }

    def find_class(self, module, name):
        if (module, name) in self.ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"RPA index references forbidden global {module}.{name}")


def read_rpa_index(path):
    """Return {name: (offset, length, prefix)} for an RPA-3.0 archive."""
    with open(path, "rb") as f:
        header = f.readline().split()
        if not header or header[0] != b"RPA-3.0":
            sys.exit(f"{path}: not an RPA-3.0 archive (header {header[:1]!r})")
        offset, key = int(header[1], 16), int(header[2], 16)
        f.seek(offset)
        raw = _IndexUnpickler(io.BytesIO(zlib.decompress(f.read()))).load()

    index = {}
    for name, entries in raw.items():
        if len(entries) != 1:
            sys.exit(f"{name}: split RPA entries are not supported")
        entry = entries[0]
        prefix = entry[2] if len(entry) > 2 else b""
        if isinstance(prefix, str):
            prefix = prefix.encode("latin-1")
        index[name] = (entry[0] ^ key, entry[1] ^ key, prefix)
    return index


def extract_entry(archive, offset, length, prefix, dest):
    """Copy one entry out in chunks. As in Ren'Py's loader, length excludes prefix."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    archive.seek(offset)
    with open(dest, "wb") as out:
        out.write(prefix)
        remaining = length
        while remaining:
            chunk = archive.read(min(CHUNK, remaining))
            if not chunk:
                raise IOError(f"{dest.name}: archive truncated")
            out.write(chunk)
            remaining -= len(chunk)


# --- progress display -----------------------------------------------------
#
# Bars and spinners with rich in an interactive terminal; plain lines
# otherwise (logs, CI, or a script run without rich installed).

def _rich():
    if not sys.stdout.isatty():
        return None
    try:
        import rich.progress
        return rich.progress
    except ImportError:
        return None


class _Plain:
    """Fallback: a line at every 10% (bars) or at start and end (spinners)."""

    def __init__(self, description, total, unit):
        self.description, self.total, self.unit, self.done, self.shown = description, total, unit, 0, -1
        print(f"{description}...", flush=True)

    def advance(self, n=1, current=None):
        self.done += n
        if self.total:
            tenth = int(10 * self.done / self.total)
            if tenth > self.shown:
                self.shown = tenth
                done, total = (f"{self.done / 2**20:.0f}", f"{self.total / 2**20:.0f} MiB") if self.unit == "bytes" \
                    else (self.done, self.total)
                print(f"  {self.description}: {done}/{total}", flush=True)

    def update(self, description):
        pass

    def log(self, message):
        print(message, flush=True)


class _Rich:

    def __init__(self, rp, description, total, unit):
        from rich.table import Column

        columns = [rp.SpinnerColumn(), rp.TextColumn("[bold]{task.description}")]
        if total is None:
            columns += [rp.TimeElapsedColumn()]
        else:
            columns += [rp.BarColumn(bar_width=24)]
            if unit == "bytes":
                columns += [rp.DownloadColumn(binary_units=True), rp.TransferSpeedColumn()]
            else:
                columns += [rp.MofNCompleteColumn()]
            columns += [rp.TimeRemainingColumn()]
        # Spinners vanish when done; bars stay to show the result.
        self.progress = rp.Progress(*columns, transient=total is None)
        # The item being worked on, if the terminal has room for it.
        if total is not None and self.progress.console.width >= 110:
            self.progress.columns += (rp.TextColumn("[dim]{task.fields[current]}",
                                                    table_column=Column(no_wrap=True, overflow="ellipsis",
                                                                        max_width=40)),)
        self.task = self.progress.add_task(description, total=total, current="")

    def advance(self, n=1, current=None):
        if current is not None:
            self.progress.update(self.task, advance=n, current=current)
        else:
            self.progress.update(self.task, advance=n)

    def update(self, description):
        self.progress.update(self.task, description=description)

    def log(self, message):
        self.progress.console.print(message, markup=False, highlight=False)


class progress:
    """A progress bar (total given) or a spinner with elapsed time (total None).

        with progress("Encoding movies", total=len(jobs)) as bar:
            bar.advance(1, current=name)
        with progress("Uploading", total=nbytes, unit="bytes") as bar: ...
        with progress("Building FFmpeg") as spinner:
            spinner.update("Building FFmpeg: make")

    bar.log() prints a line above the bar.
    """

    def __init__(self, description, total=None, unit=None):
        self.args = (description, total, unit)

    def __enter__(self):
        rp = _rich()
        if rp is None:
            self.display = _Plain(*self.args)
        else:
            self.display = _Rich(rp, *self.args)
            self.display.progress.start()
        return self.display

    def __exit__(self, *exc):
        if isinstance(self.display, _Rich):
            self.display.progress.stop()
        return False
