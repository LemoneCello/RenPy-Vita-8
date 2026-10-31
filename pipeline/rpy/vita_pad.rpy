# The Vita's buttons reach Ren'Py as a game controller. Some games switch
# controllers off for PC players (`preferences.pad_enabled = False` in their
# init code), which leaves the Vita with touch only. Ren'Py starts the
# controller once, when it creates its interface right after init code, so
# turning the preference back on last thing at init is enough. "all" (also
# allow blocklisted controllers) is kept.

init 999 python:
    if not _preferences.pad_enabled:
        _preferences.pad_enabled = True
