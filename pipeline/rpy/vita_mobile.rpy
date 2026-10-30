# Game code sees renpy.mobile as True on the Vita.
#
# Games with an Android build often switch to touch layouts with
# `if renpy.mobile:` (the test game's lock-picking minigame: the top half of the screen
# aims the pick, the bottom half turns the lock; without it a touch does both
# at once). The port leaves renpy.mobile False, as it isn't Android or iOS.
#
# The store's `renpy` is renpy.exports, which keeps its own copy of the flag;
# the engine reads the renpy package's (script loading and other internals),
# which stays False. Ren'Py's Android emulator sets the flag the same way.

init -1500 python:

    if renpy.variant("vita"):
        renpy.mobile = True
