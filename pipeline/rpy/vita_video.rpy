# The Vita port plays movies through Sony's AvPlayer (renpy.audio.vitahw),
# which only handles MP4 (H.264 + AAC). Games usually ship WebM, so the build
# converts each movie to an .mp4 next to it, and this makes the video channel
# play X.mp4 whenever the game asks for X.webm (or another extension) and the
# .mp4 exists. Movie(play=...) images also count X.webm as loadable when
# X.mp4 is (Movie keeps play only if the file exists, and the build doesn't
# ship the original), and a Movie without size= gets the original movie's
# resolution from vita_video_sizes.json (written by vita_video.py), as it
# would otherwise be drawn at the smaller re-encoded size. Game scripts stay
# unchanged.

init -1500 python:

    def _vita_install_video():
        import os

        if not getattr(renpy, "vita", False):
            return

        try:
            import renpy.audio.vitahw as vitahw
        except ImportError:
            return

        def as_mp4(filename):
            if not isinstance(filename, str):
                return filename

            base, ext = os.path.splitext(filename)
            if ext.lower() == ".mp4":
                return filename

            # Asks the loader (its file listing), not the memory card: a stat
            # per Movie definition cost ~1.8 s of boot for 462 movies.
            mp4 = base + ".mp4"
            if renpy.loader.loadable(mp4, tl=False):
                return mp4

            return filename

        cls = vitahw.VitaVideoChannel
        original_enqueue = cls.enqueue

        def enqueue(self, filenames, *args, **kwargs):
            return original_enqueue(self, [ as_mp4(f) for f in filenames ], *args, **kwargs)

        cls.enqueue = enqueue

        Movie = renpy.display.video.Movie
        original_any_loadable = Movie.any_loadable

        def any_loadable(self, name):
            if original_any_loadable(self, name):
                return True

            names = [ name ] if isinstance(name, str) else list(name)
            mp4 = [ as_mp4(i) for i in names ]

            if mp4 == names:
                return False

            return original_any_loadable(self, mp4)

        Movie.any_loadable = any_loadable

        try:
            import json
            with renpy.exports.open_file("vita_video_sizes.json") as f:
                sizes = json.loads(f.read().decode("utf-8"))
        except Exception:
            sizes = { }

        original_init = Movie.__init__

        def __init__(self, *args, **kwargs):
            original_init(self, *args, **kwargs)

            play = self._original_play
            if self.size is None and isinstance(play, str) and play in sizes:
                self.size = tuple(sizes[play])

        if sizes:
            Movie.__init__ = __init__

    _vita_install_video()
