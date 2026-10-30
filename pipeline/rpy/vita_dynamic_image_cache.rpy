# Ren'Py resolves style-prefixed image names such as the default GUI's
# "gui/button/quick_[prefix_]background.png" (DynamicImage) again on every
# interaction, for every button that uses them: it substitutes each prefix
# in turn, checks which file exists, and builds a new Image that it then
# discards because it equals the old one. On the Vita this was about half of
# the per-interaction displayable walk (~40 ms per dialogue line).
#
# When the only substitution in the names is [prefix_] and a prefix is given,
# the result depends on nothing but the names, the prefix and the game's
# files, so it's cached here, including the displayable itself (find_target
# then sees the same target and does nothing). Names with any other [substitution] go through
# Ren'Py as before, as do lookups that find nothing (Ren'Py reports those).

init -1500 python:

    def _vita_install_dynamic_image_cache():
        import re

        easy = renpy.easy

        if getattr(easy, "_vita_dynamic_image_cache", None) is not None:
            return

        original = easy.dynamic_image
        cache = { }
        only_prefix = re.compile(r"\[(?!prefix_\])")

        def cacheable(d, prefix):
            if isinstance(d, list):
                names = d
            else:
                names = [ d ]

            for i in names:
                if not isinstance(i, str) or only_prefix.search(i):
                    return None

                # Without a prefix, [prefix_] comes from the caller's scope.
                if prefix is None and "[" in i:
                    return None

            return tuple(names)

        def dynamic_image(d, scope=None, prefix=None, search=None):
            key = cacheable(d, prefix)

            if key is None:
                return original(d, scope, prefix=prefix, search=search)

            key = (key, prefix)
            rv = cache.get(key)

            if rv is None:
                rv = original(d, scope, prefix=prefix, search=search)
                if rv is not None:
                    cache[key] = rv

            return rv

        easy.dynamic_image = dynamic_image
        easy._vita_dynamic_image_cache = cache

    _vita_install_dynamic_image_cache()
