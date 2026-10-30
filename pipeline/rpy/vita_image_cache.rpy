# In "less memory" mode (RENPY_LESS_MEMORY, which the port's launcher always
# sets on the Vita) the port's Ren'Py leaves the image cache limit at 0,
# whatever config.image_cache_size_mb says. Ren'Py then keeps nothing beyond
# the images on screen and stops preloading at once ("Overfull" in its image
# cache log), so every new scene image is decoded on the main thread when it
# appears: on the Vita ~250 ms for the image plus its upload, on top of the
# line's normal cost.
#
# This gives the cache a limit sized for the Vita (in megabytes of image
# data, as config.image_cache_size_mb counts it). With the Vita's half-size
# images a full-screen picture is about 2.7 MB of that. Measured headroom:
# ~136 MiB of heap stays free during play with the cache at 0.

init -1500 python:

    def _vita_install_image_cache(megabytes):
        Cache = renpy.display.im.Cache

        if getattr(Cache, "_vita_image_cache", False):
            return

        original_init = Cache.init

        def init(self):
            original_init(self)
            if getattr(renpy, "vita", False) and not self.cache_limit:
                self.cache_limit = int(megabytes * 1024 * 1024 // 4)

        Cache.init = init
        Cache._vita_image_cache = True

    _vita_install_image_cache(48)
