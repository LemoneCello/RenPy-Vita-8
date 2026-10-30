# Diagnostic only, never ship: extra markers for Ren'Py's frame profiler
# (config.profile, see zz_vita_profile.rpy) inside interact_core's setup,
# between "final predict" and "start of interact while loop". Each wraps a
# module-level function interact_core calls in that stretch.

init python:

    def _zzpl_install():
        import renpy.display.behavior as behavior
        import renpy.display.focus as focus
        import renpy.display.video as video
        import renpy.audio.audio as audio

        def after(module, name, marker):
            original = getattr(module, name)

            def wrapper(*args, **kwargs):
                rv = original(*args, **kwargs)
                renpy.plog(1, marker)
                return rv

            setattr(module, name, wrapper)

        def before(module, name, marker):
            original = getattr(module, name)

            def wrapper(*args, **kwargs):
                renpy.plog(1, marker)
                return original(*args, **kwargs)

            setattr(module, name, wrapper)

        # Called right after the first prediction step.
        before(video, "early_interact", "zz first predict step")
        # Around root_widget.visit_all(per_interact); "zz first predict step" also
        # covers building the layers and transitions.
        after(behavior, "input_pre_per_interact", "zz pre per_interact")
        before(behavior, "input_post_per_interact", "zz per_interact visit")
        after(focus, "before_interact", "zz focus")
        before(audio, "interact", "zz misc")
        after(audio, "interact", "zz audio interact")

    _zzpl_install()
