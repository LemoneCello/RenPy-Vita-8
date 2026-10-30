# Lazy image definitions for the files under images/.
#
# Ren'Py defines an image for every file under images/ when it starts
# (_scan_images_directory in renpy/common/00images.rpy), building an
# im.Image for each. On the Vita that costs ~350 microseconds per image:
# ~4.5 s of the test game's boot for ~13,000 images, nearly all of which a session
# never shows.
#
# This defines the same names in the same order, but registers placeholders:
# instances of an im.Image subclass that only remember their file name. On
# the first access to any attribute, a placeholder turns itself into a plain
# im.Image (it changes its own class, then runs im.Image's constructor, with
# whatever hooks are installed on it), so everything that holds it sees an
# ordinary image from then on. isinstance checks see an im.Image throughout;
# pickling looks up attributes first, so saves only ever contain plain images.
#
# Only used where a file name would become an im.Image anyway: names that
# Ren'Py would treat differently (dynamic "[...]" names, displayable prefixes)
# or a game that redefines Image are defined as before.

init -1000 python:

    def _vita_install_lazy_images():
        import builtins
        import os
        import threading

        # In Ren'Py scripts `object` is Ren'Py's revertable object, whose
        # __setattr__ records rollback state; the plain one is needed here.
        object = builtins.object

        im = renpy.display.im
        Image = im.Image

        # Placeholders are realized from both the main thread and the image
        # preload thread: the one that gets there first constructs the image
        # while holding this lock, and the other waits for it.
        lock = threading.RLock()

        class VitaLazyImage(Image):
            """
            An im.Image that is constructed on first use.
            """

            __slots__ = ( )

            def __init__(self, filename):
                object.__getattribute__(self, "__dict__")["_vita_lazy_filename"] = filename

            def __getattribute__(self, name):
                d = object.__getattribute__(self, "__dict__")

                if "_vita_lazy_filename" in d:
                    with lock:
                        filename = d.get("_vita_lazy_filename")

                        # The constructor's own attribute accesses come back
                        # here (same thread, lock re-entered) and pass through.
                        if filename is not None and not d.get("_vita_lazy_busy"):
                            d["_vita_lazy_busy"] = True
                            try:
                                Image.__init__(self, filename)
                            finally:
                                del d["_vita_lazy_busy"]

                            # The constructor named the identity after this
                            # class; a plain Image of the same file must be
                            # equal to it.
                            d["identity"] = ("Image",) + tuple(d["identity"][1:])
                            del d["_vita_lazy_filename"]

                            # Only now: other threads see the class change
                            # without coming through here.
                            object.__setattr__(self, "__class__", Image)

                return object.__getattribute__(self, name)

            def __reduce_ex__(self, protocol):
                # copy and pickle take the type before the state; realize
                # first, so copies and saves hold plain images.
                self.filename
                return Image.__reduce_ex__(self, protocol)

        im.VitaLazyImage = VitaLazyImage

        # Same as _scan_images_directory in 00images.rpy, apart from the
        # placeholders, and with the per-file work trimmed (it runs ~13,000
        # times): config read once, names split with string operations, and
        # renpy.has_image(exact=True) / renpy.image done directly on the
        # image tables, as those functions do.
        def scan_images_directory():

            if not config.images_directory:
                return

            prefix = config.images_directory.rstrip('/') + '/'

            lazy = renpy.store.Image is im.image
            extensions = frozenset(config.image_extensions)
            dynamic = config.dynamic_images
            lookup_prefix = renpy.easy.lookup_displayable_prefix
            images = renpy.display.image.images
            register_image = renpy.display.image.register_image
            splitext = os.path.splitext

            for fn in renpy.list_files():
                if not fn.startswith(prefix):
                    continue

                basename = fn.rpartition("/")[2]
                base, ext = splitext(basename)

                if not ext.lower() in extensions:
                    continue

                base = base.lower()
                base = base.partition("@")[0]

                name = tuple(base.split())

                if not name or name in images:
                    if name:
                        continue
                    # An empty name: let Ren'Py report it as it would.
                    renpy.image(base, fn)
                    continue

                if lazy and not ("[" in fn and dynamic) and lookup_prefix(fn) is None and fn[0] != "#":
                    register_image(name, VitaLazyImage(fn))
                else:
                    renpy.image(base, fn)

        store._scan_images_directory = scan_images_directory

    if renpy.variant("vita") or "VITA_LAZY_IMAGES_TEST" in __import__("os").environ:
        _vita_install_lazy_images()
