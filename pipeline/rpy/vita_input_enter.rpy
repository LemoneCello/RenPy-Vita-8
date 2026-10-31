# Enter in a text input, for games made before Ren'Py 8.3 / 7.8.
#
# Until then, Enter in an input whose value isn't returnable (e.g.
# VariableInputValue("player")) went on to the rest of the screen, so a
# screen's `key "K_RETURN" action ...` (or its default button) took it:
# that's how many name-entry screens accept the name. Ren'Py 8.3 swallows
# the event instead (commit 2bed8513f, "Allow input to take an action").
# On the Vita this left such screens stuck: the keyboard's Enter key sends
# Return and closes the keyboard, the input swallowed it, and Ren'Py opened
# the keyboard again for the still-active input.
#
# For games made before 8.3/7.8 (config.script_version from
# script_version.txt; None means the game predates that file, which Ren'Py
# writes since 7.0, so it's old too), Enter is passed on again when the
# input itself has nothing to do with it.

init 999 python:

    def _vita_input_enter_old_game():
        version = config.script_version
        if version is None:
            return True
        return _compat_versions(tuple(version), (7, 7, 99), (8, 2, 99))

    if _vita_input_enter_old_game():

        import renpy.display.behavior as _vita_behavior
        import renpy.display.core as _vita_display_core

        _vita_input_event = _vita_behavior.Input.event

        def _vita_input_event_compat(self, ev, x, y, st):
            try:
                return _vita_input_event(self, ev, x, y, st)
            except _vita_display_core.IgnoreEvent:
                if (self.action is None and self.value is not None
                        and _vita_behavior.map_event(ev, "input_enter")
                        and not (self.multiline and _vita_behavior.map_event(ev, "input_next_line"))):
                    return None
                raise

        _vita_behavior.Input.event = _vita_input_event_compat
