"""Shared helpers for the stage scripts (imported, not run)."""

import io
import pickle
import struct
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


# --- Compiled scripts (.rpyc) ---------------------------------------------
#
# Some games ship only compiled scripts. The stages that look through the
# scripts' text (screen size, title, characters for fonts, movie and image
# references) then read a rendering of the .rpyc as Ren'Py-like source: the
# statements they look for in their usual form (define, python blocks, image,
# show/scene/hide, play via the original line of user statements, say,
# menu...), and every other string a statement holds in a comment line.
#
# The pickle is read without Ren'Py: its classes become inert stand-ins (no
# code from the file runs), so this works for scripts compiled by any Ren'Py
# from 6.18 on, under Python 2 or 3.

class _Stub:
    """Stand-in for a Ren'Py class; keeps constructor args and state."""

    def __new__(cls, *args, **kwargs):
        obj = object.__new__(cls)
        obj._args = args
        return obj

    def __setstate__(self, state):
        # Ren'Py's slotted objects pickle as (dict or None, {slot: value}).
        if (isinstance(state, tuple) and len(state) == 2
                and (state[0] is None or isinstance(state[0], dict)) and isinstance(state[1], dict)):
            merged = dict(state[0] or {})
            merged.update(state[1])
            state = merged
        if isinstance(state, dict):
            self.__dict__.update(state)
        else:
            self._state = state



class _StubStr(str):
    """Stand-in for str subclasses (PyExpr): the text plus the args."""

    def __new__(cls, s="", *args, **kwargs):
        obj = str.__new__(cls, s)
        obj._args = args
        return obj

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)


class _StubContainer:
    """Mixin for container stand-ins: constructor args (a defaultdict's
    factory...) are kept aside; the items arrive later from the pickle."""

    def __init__(self, *args, **kwargs):
        self._args = args

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)


class _StubList(_StubContainer, list):
    pass


class _StubDict(_StubContainer, dict):
    pass


class _StubSet(_StubContainer, set):
    pass


class _ScriptUnpickler(pickle.Unpickler):
    """Real classes only for plain containers; stand-ins for the rest."""

    REAL = {
        ("builtins", n) for n in ("set", "frozenset", "list", "dict", "tuple", "bytes", "bytearray",
                                  "str", "int", "float", "complex", "bool", "slice", "object")
    } | {("copyreg", "_reconstructor"), ("_codecs", "encode"), ("collections", "OrderedDict")}

    @staticmethod
    def _base(name):
        """Containers (RevertableList, defaultdict...) need to take their items."""
        lower = name.lower()
        if name == "PyExpr":
            return _StubStr
        for suffix, base in (("list", _StubList), ("dict", _StubDict), ("set", _StubSet)):
            if lower.endswith(suffix):
                return base
        return _Stub

    def __init__(self, f):
        super().__init__(f, fix_imports=True, encoding="utf-8", errors="surrogateescape")
        self.classes = {}

    def find_class(self, module, name):
        if (module, name) in self.REAL:
            return super().find_class(module, name)
        key = (module, name)
        if key not in self.classes:
            base = self._base(name)
            self.classes[key] = type(name, (base,), {"_module": module})
        return self.classes[key]


def read_rpyc(data):
    """The statements of a compiled script (bytes of a .rpyc/.rpymc), as stand-ins."""
    if data.startswith(b"RENPY RPC2"):
        pos, raw = 10, None
        while True:
            slot, start, length = struct.unpack("<III", data[pos:pos + 12])
            if slot == 0:
                break
            if slot == 1:
                raw = zlib.decompress(data[start:start + length])
                break
            pos += 12
        if raw is None:
            raise ValueError("no statements slot in the compiled script")
    else:
        raw = zlib.decompress(data)  # before Ren'Py 6.18: the bare pickle
    _data, stmts = _ScriptUnpickler(io.BytesIO(raw)).load()
    return stmts


def _cls(obj):
    return type(obj).__name__


def _source(code):
    """Python source of a PyCode or PyExpr stand-in (or a plain string)."""
    if code is None:
        return ""
    if isinstance(code, str):
        return str(code)
    state = getattr(code, "_state", None)
    if isinstance(state, tuple) and len(state) > 1:
        return _source(state[1])  # PyCode: (version, source, location, mode[, py])
    source = getattr(code, "source", None)
    return _source(source) if source is not None else ""


# Attributes that say where a statement is, or link to the next one.
_SKIP_ATTRS = {"next", "filename", "linenumber", "location", "loc", "serial", "_module"}


def _shown(attr, value):
    # A statement without a label is named by (file, time, serial).
    return attr not in _SKIP_ATTRS and not (attr == "name" and isinstance(value, tuple))


def _strings(obj, seen=None):
    """Every string inside obj (attribute values, not names; not the
    statement's position or the link to the next statement)."""
    if seen is None:
        seen = set()
    if isinstance(obj, str):
        yield str(obj)  # for a PyExpr, its text, not its position
        return
    if isinstance(obj, (int, float, bytes, type, type(None))) or id(obj) in seen:
        return
    seen.add(id(obj))
    if _cls(obj) == "PyCode":
        yield _source(obj)  # not its location
        return
    if isinstance(obj, dict):
        items = list(obj.keys()) + list(obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        items = list(obj)
    else:
        items = []
    attrs = getattr(obj, "__dict__", None)
    if attrs:
        items += [v for k, v in attrs.items() if _shown(k, v)]
    for args in (getattr(obj, "_args", ()), getattr(obj, "_state", ())):
        if isinstance(args, tuple):
            items += list(args)
    for item in items:
        yield from _strings(item, seen)


def _quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _store_prefix(store):
    store = store or "store"
    return "" if store == "store" else store[len("store."):] + "."


def _imspec(imspec):
    """show/scene/hide arguments from an imspec tuple."""
    if not imspec:
        return ""
    name, expression, tag = imspec[0], imspec[1], imspec[2]
    at_list = imspec[3] if len(imspec) > 3 else []
    text = f"expression {_source(expression)}" if expression else " ".join(name)
    if tag:
        text += f" as {tag}"
    if at_list:
        text += " at " + ", ".join(_source(a) for a in at_list)
    return text


def _render(nodes, out, indent=0):
    pad = "    " * indent
    for node in nodes or ():
        _render_node(node, out, indent, pad)


def _block(node, out, indent, attr="block"):
    _render(getattr(node, attr, None), out, indent + 1)


def _rest(obj, out, pad, skip=()):
    """Strings a statement holds that its rendering doesn't show."""
    values = [v for k, v in vars(obj).items() if k not in skip and _shown(k, v)]
    strings = [s for s in _strings(values) if s.strip()]
    if strings:
        out.append(f"{pad}# " + " ".join(" ".join(s.split()) for s in strings))


def _render_node(node, out, indent, pad):
    kind = _cls(node)
    if kind in ("Init",):
        out.append(f"{pad}init {getattr(node, 'priority', 0)}:")
        _block(node, out, indent)
    elif kind in ("Python", "EarlyPython"):
        out.append(f"{pad}python{' early' if kind == 'EarlyPython' else ''}:")
        for line in _source(node.code).splitlines():
            out.append(f"{pad}    {line}")
    elif kind in ("Define", "Default"):
        word = kind.lower()
        out.append(f"{pad}{word} {_store_prefix(getattr(node, 'store', None))}{node.varname} = {_source(node.code)}")
    elif kind == "Image":
        name = " ".join(node.imgname)
        if getattr(node, "code", None) is not None:
            out.append(f"{pad}image {name} = {_source(node.code)}")
        else:
            out.append(f"{pad}image {name}:")
            _rest(node, out, pad + "    ", skip=("imgname", "code"))
    elif kind in ("Show", "Scene", "Hide"):
        out.append(f"{pad}{kind.lower()} {_imspec(getattr(node, 'imspec', None))}".rstrip())
        if getattr(node, "atl", None) is not None:
            _rest(node, out, pad + "    ", skip=("imspec",))
    elif kind == "Say":
        who = f"{node.who} " if getattr(node, "who", None) else ""
        out.append(f"{pad}{who}{_quote(str(node.what))}")
    elif kind == "Menu":
        out.append(f"{pad}menu:")
        for item in node.items or ():
            label, condition, block = item[0], item[1], item[2]
            cond = _source(condition)
            out.append(f"{pad}    {_quote(str(label))}" + (f" if {cond}" if cond and cond != "True" else "")
                       + (":" if block is not None else ""))
            _render(block, out, indent + 2)
    elif kind == "Label":
        out.append(f"{pad}label {node.name}:")
        _block(node, out, indent)
    elif kind == "If":
        for i, (condition, block) in enumerate(node.entries):
            cond = _source(condition)
            out.append(f"{pad}" + ("if" if i == 0 else "elif") + f" {cond}:")
            _render(block, out, indent + 1)
    elif kind == "While":
        out.append(f"{pad}while {_source(node.condition)}:")
        _block(node, out, indent)
    elif kind in ("Jump", "Call"):
        target = node.label if kind == "Call" else node.target
        expr = "expression " if getattr(node, "expression", False) else ""
        out.append(f"{pad}{kind.lower()} {expr}{_source(target)}")
    elif kind == "Return":
        out.append(f"{pad}return {_source(getattr(node, 'expression', None))}".rstrip())
    elif kind == "Pass":
        out.append(f"{pad}pass")
    elif kind == "With":
        expr = _source(node.expr)
        if expr != "None":
            out.append(f"{pad}with {expr}")
    elif kind == "UserStatement":
        out.append(f"{pad}{node.line}")
        if getattr(node, "block", None):
            _rest(node, out, pad + "    ", skip=("line", "parsed"))
    elif kind in ("Translate", "TranslateBlock", "TranslateEarlyBlock"):
        out.append(f"{pad}translate {getattr(node, 'language', None)} {getattr(node, 'identifier', '')}:")
        _block(node, out, indent)
    else:
        # Screens, styles, transforms, translations of strings, ATL...: their
        # strings, so text and file names in them are still seen.
        name = getattr(node, "varname", None) or getattr(node, "style_name", None) or getattr(node, "name", "")
        out.append(f"{pad}# {kind.lower()} {name if isinstance(name, str) else ''}".rstrip())
        _rest(node, out, pad)


def rpyc_text(data):
    """Ren'Py-like source for a compiled script (bytes of a .rpyc/.rpymc)."""
    out = []
    _render(read_rpyc(data), out)
    return "\n".join(out) + "\n"


def _pycode_versions(obj, seen):
    """Python versions (2 or 3) of the PyCode stand-ins inside obj."""
    if isinstance(obj, (str, int, float, bytes, type, type(None))) or id(obj) in seen:
        return
    seen.add(id(obj))
    if _cls(obj) == "PyCode":
        state = getattr(obj, "_state", None)
        if isinstance(state, tuple) and len(state) >= 4:
            # Ren'Py before 7.4 stored no version: its code is Python 2.
            yield state[4] if len(state) > 4 else 2
        return
    if isinstance(obj, dict):
        items = list(obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        items = list(obj)
    else:
        items = []
    attrs = getattr(obj, "__dict__", None)
    if attrs:
        items += [v for k, v in attrs.items() if k != "next"]
    state = getattr(obj, "_state", None)
    if isinstance(state, tuple):
        items += list(state)
    for item in items:
        yield from _pycode_versions(item, seen)


def scripts_python(game_dir):
    """The Python version (2 or 3) of the code in the compiled scripts under
    game_dir: 2 for a game made with Ren'Py 7 or older. None if they hold no
    Python. The first compiled script with Python decides: a game's scripts
    are compiled by one Ren'Py."""
    for path in sorted(game_dir.rglob("*.rpyc")):
        for version in _pycode_versions(read_rpyc(path.read_bytes()), set()):
            return version
    return None


def script_texts(game_dir, modules=True):
    """(path, text) for each script under game_dir: the .rpy (and, with modules,
    .rpym) sources, plus a rendering of each compiled script with no source
    beside it (see rpyc_text)."""
    exts = {".rpy": ".rpyc", ".rpym": ".rpymc"} if modules else {".rpy": ".rpyc"}
    for path in sorted(game_dir.rglob("*")):
        suffix = path.suffix.lower()
        if not path.is_file():
            continue
        if suffix in exts:
            yield path, path.read_text(encoding="utf-8", errors="replace")
        elif suffix in exts.values() and not path.with_suffix(suffix[:-1]).exists():
            yield path, rpyc_text(path.read_bytes())


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
