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
