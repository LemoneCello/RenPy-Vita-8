# Ren'Py gives every Movie(play=...) its own audio channel
# (config.auto_movie_channel) and registers it as soon as the Movie is
# created, i.e. at init for every movie image the game defines. Ren'Py then
# loops over all channels about 20 times a second (the periodic audio pass,
# plus a context lookup per channel on the main thread) and once more per
# interaction. A game with hundreds of movie images (the test game: 462) pays for
# all of them all the time, which is cheap on a PC but not on the Vita.
#
# This defers the registration to the first time the Movie is used: render,
# play, stop and per_interact all call ensure_channels() themselves, so
# nothing else changes. Channel names are still assigned at creation.

init -1500 python:

    def _vita_install_lazy_movie_channels():
        Movie = renpy.display.video.Movie

        if getattr(Movie, "_vita_lazy_channels", False):
            return

        original_init = Movie.__init__
        original_ensure = Movie.ensure_channels
        creating = [ 0 ]

        def __init__(self, *args, **kwargs):
            creating[0] += 1
            try:
                original_init(self, *args, **kwargs)
            finally:
                creating[0] -= 1

        def ensure_channels(self):
            if creating[0]:
                return
            original_ensure(self)

        Movie.__init__ = __init__
        Movie.ensure_channels = ensure_channels
        Movie._vita_lazy_channels = True

    _vita_install_lazy_movie_channels()
