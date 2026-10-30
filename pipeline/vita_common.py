"""Shared helpers for the stage scripts (imported, not run)."""

import io
import pickle
import sys
import zlib

CHUNK = 1 << 20


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
