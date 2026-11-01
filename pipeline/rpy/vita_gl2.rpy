# Settings for Ren'Py's gl2 renderer on the Vita (an experiment, switched on
# by a file named vita_gl2 in the app folder; see port/renpy.py).
#
# - No depth buffer: gl2 gives its framebuffer objects a 24-bit depth buffer
#   by default, which only the 3D stage (gl_depth) uses, and the Vita's GPU
#   driver is short of memory (render targets fail and the driver crashes).
#   A game that sets config.depth_size itself keeps its setting, as that
#   runs later.

init -1500 python:
    if getattr(renpy, "vita", False):
        config.depth_size = None

# The port runs the legacy renderer unless the vita_gl2 file asks for gl2, but
# a game's `define config.gl2 = True` makes Ren'Py pick gl2 regardless (Hero's
# Harem Guild sets it for an outline shader it doesn't use, and crashed in
# gl2's framebuffer code when its menu movie started). Late, after the game's
# own init code.
init 1000000 python:
    if getattr(renpy, "vita", False):
        import os as _vita_os
        if _vita_os.environ.get("RENPY_RENDERER") != "gles2":
            config.gl2 = False
