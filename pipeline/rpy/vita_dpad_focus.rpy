# D-pad focus for full-screen masked buttons.
#
# Several menus (main menu socials and exit, Return, the gallery character
# picker) draw each button as a screen-sized image that is mostly transparent,
# with focus_mask True. The mouse only hits the opaque pixels, but D-pad focus
# compares rectangles, and every such button's rectangle is the whole screen,
# so focus lands on one and can't leave it.
#
# This gives those buttons a keyboard focus rectangle equal to the opaque
# bounding box of their screen-sized idle image, used both for distances and
# for the direction test. Ren'Py's image cache already computes that box
# (CacheEntry.bounds) when it loads the image, so normally nothing extra is
# decoded. Mouse and touch focus are unchanged.
#
# It also stops one short controller press from moving focus twice when a
# frame is slow (see the PadEvent.repeat patch at the end).
#
# Not Vita-specific: it fixes keyboard and gamepad navigation on desktop too.

init -1500 python:

    def _vita_install_dpad_focus():
        # The game defines a Character called `id`, so use builtins directly.
        import builtins
        import pygame_sdl2

        im = renpy.display.im
        focus = renpy.display.focus

        # Buttons smaller than this fraction of the screen are left alone.
        min_fraction = 0.5

        # Image -> opaque bounds (x, y, w, h) and size (w, h), in virtual pixels.
        bounds_cache = { }

        def collect_images(d, rv, seen):
            if d is None or builtins.id(d) in seen:
                return

            seen.add(builtins.id(d))

            if isinstance(d, im.Image):
                rv.append(d)
                return

            if isinstance(d, im.ImageBase):
                # Image manipulators (MatrixColor etc.) wrap .image or .images.
                for c in [ getattr(d, "image", None) ] + list(getattr(d, "images", None) or [ ]):
                    if isinstance(c, im.ImageBase):
                        collect_images(c, rv, seen)
                return

            try:
                children = d.visit()
            except Exception:
                return

            for c in children:
                collect_images(c, rv, seen)

        def opaque_bounds(img):
            if img in bounds_cache:
                return bounds_cache[img]

            try:
                ce = im.cache.cache.get(img, None)

                if ce is not None and ce.bounds != (0, 0, ce.width, ce.height):
                    # Undo the padding Ren'Py adds around texture bounds
                    # (im.expand_bounds), except where it was clipped.
                    e = config.expand_texture_bounds
                    x, y, w, h = ce.bounds
                    x0 = x + e if x > 0 else x
                    y0 = y + e if y > 0 else y
                    x1 = x + w - e if x + w < ce.width else x + w
                    y1 = y + h - e if y + h < ce.height else y + h
                    bounds = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
                    size = (ce.width, ce.height)
                else:
                    # Not cached yet, or texture bounds optimisation is off.
                    # (cache.get() won't help: without config.cache_surfaces
                    # it drops entries that have no texture.)
                    surf = img.load()
                    bounds = tuple(surf.get_bounding_rect())
                    size = surf.get_size()

            except Exception:
                return None

            scale = 1.0 / (img.get_oversample() or 1.0)
            rv = (tuple(v * scale for v in bounds), (size[0] * scale, size[1] * scale))

            bounds_cache[img] = rv
            return rv

        class MaskRect(tuple):
            """
            A focus rectangle taken from a masked button's opaque bounds.
            """

            __slots__ = ()

        def mask_rect(f):
            """
            The opaque-area rectangle for a full-screen focus_mask button, or
            None to use Ren'Py's normal rectangle.
            """

            if f.x is None or f.x is False or f.w is None or f.h is None:
                return None

            style = f.widget.style

            if not style.focus_mask or style.keyboard_focus_insets is not None:
                return None

            if f.w < config.screen_width * min_fraction or f.h < config.screen_height * min_fraction:
                return None

            # Cached on the widget: walking its displayables is slow on the Vita.
            cached = getattr(f.widget, "_vita_focus_bounds", None)
            if cached is not None and cached[0] == (f.w, f.h):
                bounds = cached[1]
            else:
                bounds = idle_bounds(f)
                f.widget._vita_focus_bounds = ((f.w, f.h), bounds)

            if bounds is None:
                return None

            bx, by, bw, bh = bounds
            return MaskRect((f.x + bx, f.y + by, bw, bh))

        def idle_bounds(f):
            """
            The opaque bounds of the button's idle image, relative to the
            button, or None.
            """

            images = [ ]
            collect_images(f.widget, images, set())

            # The first image that fills the button is the idle one (hover
            # images often add a larger glow), and sits at the button's origin.
            for img in images:
                info = opaque_bounds(img)
                if info is None:
                    continue

                (bx, by, bw, bh), (iw, ih) = info

                if abs(iw - f.w) > 2 or abs(ih - f.h) > 2 or bw <= 0 or bh <= 0:
                    continue

                return (bx, by, bw, bh)

            return None

        orig_inset_rect = focus.Focus.inset_rect

        def inset_rect(self):
            rv = mask_rect(self)
            if rv is not None:
                return rv
            return orig_inset_rect(self)

        focus.Focus.inset_rect = inset_rect

        # In Ren'Py 8.3.2 (desktop), focus_nearest measures distances with
        # inset_rect, but its direction test (e.g. "new is below old") reads
        # the raw rectangles, which are full-screen here. 8.3.4 (the Vita
        # port) already passes the test inset rectangles (as tuples, in locals
        # from_rect/to_rect).
        rect_condition = "from_rect" in focus.focus_nearest.__code__.co_varnames

        class InsetRect(object):
            __slots__ = ("x", "y", "w", "h", "rect")

            def __init__(self, f):
                if f.x is None or f.x is False:
                    self.rect = None
                    self.x, self.y, self.w, self.h = f.x, f.y, f.w, f.h
                else:
                    self.rect = f.inset_rect()
                    self.x, self.y, self.w, self.h = self.rect

        # The direction test also wants the new button to start beyond the old
        # one's far edge. Masked buttons are often slanted or irregular shapes
        # whose bounding boxes overlap their neighbours' a little (the test
        # game's character cards by 40 of 275 pixels), so that test skipped every
        # other one. Between two buttons where either rectangle comes from a
        # mask, an overlap of up to this fraction of the narrower one counts.
        mask_overlap = 0.25

        def mask_overlap_ok(old, new, axis, sign):
            if not (isinstance(old, MaskRect) or isinstance(new, MaskRect)):
                return False

            op, os_ = old[axis], old[axis + 2]
            np, ns = new[axis], new[axis + 2]
            slack = mask_overlap * min(os_, ns)

            if sign > 0:
                return np > op and op + os_ - np <= slack
            else:
                return np < op and np + ns - op <= slack

        orig_focus_nearest = focus.focus_nearest

        def focus_nearest(from_x0, from_y0, from_x1, from_y1,
                          to_x0, to_y0, to_x1, to_y1,
                          line_dist, condition,
                          xmul, ymul, wmul, hmul):

            # The movement's axis and direction, from key_handler's scoring
            # multipliers: right (-1, 0), left (1, 0), up (0, 1), down (0, -1).
            if xmul:
                axis, sign = 0, -xmul
            else:
                axis, sign = 1, -ymul

            def mask_condition(old, new):
                if not rect_condition:
                    old = InsetRect(old)
                    new = InsetRect(new)

                if condition(old, new):
                    return True

                if not rect_condition:
                    if old.rect is None or new.rect is None:
                        return False
                    old, new = old.rect, new.rect

                return mask_overlap_ok(old, new, axis, sign)

            return orig_focus_nearest(from_x0, from_y0, from_x1, from_y1,
                                      to_x0, to_y0, to_x1, to_y1,
                                      line_dist, mask_condition,
                                      xmul, ymul, wmul, hmul)

        focus.focus_nearest = focus_nearest

        # Same as Ren'Py's focus_extreme, which picks the first button when
        # nothing is focused yet, but scoring the inset rectangles.
        def focus_extreme(xmul, ymul, wmul, hmul):
            max_focus = None
            max_score = -(65536 ** 2)

            for f in focus.focus_list:

                if not f.widget.style.keyboard_focus:
                    continue

                if f.x is None:
                    continue

                x, y, w, h = f.inset_rect()

                score = x * xmul + y * ymul + w * wmul + h * hmul

                if score > max_score:
                    max_score = score
                    max_focus = f

            if max_focus:
                return focus.change_focus(max_focus)

        focus.focus_extreme = focus_extreme

        # Keep D-pad focus on a button across what its action does. Rollback
        # (the quick menu's Back) and other context changes clear the focus,
        # and the next interaction gave it to nothing (or to a placeless,
        # actionless widget), so pressing Back again took a fresh trip to the
        # quick menu each time. Remember the last button focused while the
        # player wasn't using the mouse or touch, by screen and Ren'Py's focus
        # name, and refocus it when an interaction starts with no button
        # focused and that button is still on screen.
        last_button = { "key" : None, "time" : 0.0 }

        orig_set_focused = focus.set_focused

        def set_focused(widget, arg, screen):
            orig_set_focused(widget, arg, screen)

            # Only focus the D-pad (or keyboard) moved; touch and mouse focus
            # (hovering) isn't remembered.
            if getattr(focus, "pending_focus_type", None) != "keyboard":
                return

            name = getattr(widget, "full_focus_name", None)
            if screen is not None and name is not None and getattr(widget, "action", None) is not None:
                last_button["key"] = (screen.screen_name, name)
                last_button["time"] = renpy.display.core.get_time()

        focus.set_focused = set_focused

        def touched_since():
            # Any touch or mouse event after the button was focused (e.g. a
            # tap to advance) ends the D-pad's claim on it.
            interface = renpy.display.interface
            return interface is not None and (interface.mouse_event_time or 0) > last_button["time"]

        orig_before_interact = focus.before_interact

        def before_interact(roots):
            orig_before_interact(roots)

            key = last_button["key"]
            current = focus.get_focused()

            if key is None:
                return

            if touched_since():
                last_button["key"] = None
                return

            if current is not None and getattr(current, "action", None) is not None:
                return

            found = [ ]

            def callback(f, n):
                screen = renpy.display.screen._current_screen
                if f is not None and screen is not None and (screen.screen_name, getattr(f, "full_focus_name", None)) == key:
                    found.append((f, screen))

            for root in roots:
                try:
                    root.find_focusable(callback, None)
                except renpy.display.layout.IgnoreLayers:
                    pass

            if not found:
                return

            widget, screen = found[0]

            if current is not None:
                renpy.display.screen.push_current_screen(focus.screen_of_focused)
                try:
                    current.unfocus(default=False)
                finally:
                    renpy.display.screen.pop_current_screen()

            set_focused(widget, None, screen)

            renpy.display.screen.push_current_screen(screen)
            try:
                widget.focus(default=False)
            finally:
                renpy.display.screen.pop_current_screen()

        focus.before_interact = before_interact

        # Controller auto-repeat: Ren'Py repeats a held button once
        # controller_first_repeat (0.25 s) has passed, checked on its PERIODIC
        # timer event. After a slow frame (a focus change on the main menu
        # takes ~0.5 s on the Vita) that event can be handled before the
        # button's release, which is already queued, so one short press moves
        # focus twice. Don't repeat while a button release is waiting.
        controller = renpy.display.controller
        orig_repeat = controller.PadEvent.repeat

        def repeat(self):
            if self.state == "press":
                try:
                    if pygame_sdl2.event.peek([ pygame_sdl2.CONTROLLERBUTTONUP ]):
                        return
                except Exception:
                    pass

            return orig_repeat(self)

        controller.PadEvent.repeat = repeat

    _vita_install_dpad_focus()
