# Python 2 games on Ren'Py 8. vita_build.py adds this file only for games
# whose compiled scripts hold Python 2 code (made with Ren'Py 7 or older).
# Ren'Py 8 already rewrites Python 2 syntax and keeps names such as unicode,
# basestring, xrange and dict.has_key; this covers what changed at runtime.
#
# Files: under Python 2, str was both text and bytes, so games read files
# with renpy.file() and use the result as text (line.split(","), comparisons
# with string literals), and also copy files with it:
#     open(path, "wb").write(renpy.file("x").read())
# Here renpy.file() and renpy.open_file() return text by default, decoded
# with surrogateescape and without newline translation, so any bytes survive
# the trip; and open() for binary writing in game code encodes such text
# back to the same bytes. An explicit encoding (False for binary) is honoured,
# which is what Ren'Py's own calls use.

python early:

    import io as _vita_py2_io
    import renpy.exports.loaderexports as _vita_py2_loader

    _vita_py2_binary_open_file = _vita_py2_loader.open_file

    def _vita_py2_open_file(fn, encoding=None, directory=None):
        f = _vita_py2_binary_open_file(fn, encoding=False, directory=directory)
        if encoding is False:
            return f
        return _vita_py2_io.TextIOWrapper(f, encoding=encoding or "utf-8", errors="surrogateescape", newline="")

    # renpy.file() calls loaderexports.open_file; game code's renpy is renpy.exports.
    _vita_py2_loader.open_file = _vita_py2_open_file
    renpy.open_file = _vita_py2_open_file

    class _VitaPy2BinaryWriter:
        """A file open for binary writing that also takes text, as
        Python 2's str, encoded back to the bytes it was read from."""

        def __init__(self, f):
            self._vita_file = f

        def write(self, data):
            if isinstance(data, str):
                data = data.encode("utf-8", "surrogateescape")
            return self._vita_file.write(data)

        def writelines(self, lines):
            for line in lines:
                self.write(line)

        def __getattr__(self, name):
            return getattr(self._vita_file, name)

        def __iter__(self):
            return iter(self._vita_file)

        def __enter__(self):
            self._vita_file.__enter__()
            return self

        def __exit__(self, *exc):
            return self._vita_file.__exit__(*exc)

    _vita_py2_builtin_open = open

    def _vita_py2_open(file, mode="r", *args, **kwargs):
        f = _vita_py2_builtin_open(file, mode, *args, **kwargs)
        if "b" in mode and any(c in mode for c in "wax+"):
            return _VitaPy2BinaryWriter(f)
        return f

    open = _vita_py2_open
