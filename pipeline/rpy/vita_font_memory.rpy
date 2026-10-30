# Ren'Py gives FreeType each font file as a Python file object: every time
# FreeType needs font data (each new glyph, and HarfBuzz shaping through
# FreeType), a callback seeks and reads that file. On the Vita those are
# reads from the memory card through Python, and they made every new glyph
# cost ~5 ms to rasterise (0.02 ms on a desktop), whatever its size or
# hinting, and shaping a 52-letter line ~50 ms.
#
# This reads a font file into memory once, when Ren'Py first loads it, so
# FreeType's reads become in-memory copies. Fonts load lazily, so only fonts
# the game actually uses take memory (the test game's dialogue fonts are a few
# hundred KB; its biggest font, 3.2 MB, is only used in a few places).
#
# Active on the Vita; VITA_FONT_MEMORY_TEST=1 turns it on elsewhere.

init -1500 python:

    def _vita_install_font_memory():
        import io
        import os
        import threading

        font = renpy.text.font

        if getattr(font, "_vita_font_memory", False):
            return

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_FONT_MEMORY_TEST")):
            return

        font._vita_font_memory = True

        loader = renpy.loader
        original_load = loader.load
        original_load_face = font.load_face

        # load_face looks fonts up with renpy.loader.load. Swapping that only
        # while the main thread is inside load_face leaves the image preload
        # thread's loads alone.
        loading = threading.local()

        def load(name, *args, **kwargs):
            f = original_load(name, *args, **kwargs)

            if not getattr(loading, "active", False):
                return f

            try:
                data = f.read()
            finally:
                f.close()

            return io.BytesIO(data)

        def load_face(fn, shaper):
            loading.active = True
            try:
                return original_load_face(fn, shaper)
            finally:
                loading.active = False

        loader.load = load
        font.load_face = load_face

    _vita_install_font_memory()
