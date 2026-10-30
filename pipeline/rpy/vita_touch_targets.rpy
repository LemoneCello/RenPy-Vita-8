# Bigger touch targets for the quick menu on the Vita.
#
# Ren'Py's default GUI (which most games start from) sizes the touch
# variant's quick menu for phones: text size 30 and borders (60, 21, 60, 0)
# in a 1920x1080 game. The Vita draws such a game at half size on a 5" screen,
# so the buttons were ~25 pixels (~3 mm) tall and hard to hit (found in
# play testing, 2026-10-29). This makes the stock quick_button style at least ~7 mm tall,
# growing it upwards (its backgrounds are usually transparent frames, so only
# the hit area grows), and the text a little larger.
#
# Only touches styles named quick_button / quick_button_text, so games with
# their own quick menu styles are unaffected.

init 1 python:

    def _vita_touch_targets():
        if not (renpy.variant("vita") and renpy.variant("touch")):
            return

        # Virtual pixels per Vita screen pixel (544 px tall screen).
        scale = config.screen_height / 544.0

        # The Vita's screen has ~8.7 pixels per mm.
        min_height = int(64 * scale)
        min_text = int(20 * scale)

        borders = getattr(gui, "quick_button_borders", None)
        if borders is not None:
            left, top, right, bottom = borders.padding
        else:
            left, top, right, bottom = 0, 0, 0, 0

        text = max(getattr(gui, "quick_button_text_size", 0) or 0, min_text)
        top = max(top, min_height - text - bottom)

        style.quick_button.padding = (left, top, right, bottom)
        style.quick_button_text.size = text

    _vita_touch_targets()

    # Ren'Py restores the styles saved before init whenever it sets the
    # language (also once at startup), which drops changes made here; its
    # language callbacks run after that, before the styles are rebuilt.
    config.change_language_callbacks.append(_vita_touch_targets)
