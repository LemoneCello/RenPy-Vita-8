# Ren'Py times every animation, transition, pause and timer with
# renpy.display.core.get_time(), so when one frame takes far longer than
# usual (an image decoded on first draw, a screen built for the first time),
# the next frame jumps ahead by that much and the animation skips. Ren'Py
# already discounts the first frame of each interaction; this does the same
# for every later frame: time spent drawing a frame beyond MAX_FRAME doesn't
# count, so a stalled animation picks up where it stopped instead of
# skipping. Animations take longer in wall time when frames stall, pauses
# and timeouts too; music and movies keep real time.
#
# Active on the Vita; VITA_STALL_CLOCK_TEST=1 turns it on elsewhere.

init -1500 python:

    def _vita_install_stall_clock():
        import os

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_STALL_CLOCK_TEST")):
            return

        core = renpy.display.core

        if getattr(core, "_vita_stall_clock", False):
            return

        core._vita_stall_clock = True

        # Frames are allowed this long before the clock stops.
        MAX_FRAME = 0.1

        real_time = core.get_time
        lost = [ 0.0 ]

        def get_time():
            return real_time() - lost[0]

        core.get_time = get_time

        original_draw_screen = core.Interface.draw_screen

        def draw_screen(self, *args, **kwargs):
            start = real_time()
            try:
                return original_draw_screen(self, *args, **kwargs)
            finally:
                excess = real_time() - start - MAX_FRAME
                if excess > 0:
                    lost[0] += excess

        core.Interface.draw_screen = draw_screen

    _vita_install_stall_clock()
