# Transform properties the legacy gles renderer (the one the Vita port uses)
# can't draw:
#
# - `perspective`: the render gets a 3D projection the legacy renderer
#   doesn't handle; the Transform's content simply doesn't appear (the test
#   game's character menu showed none of its cards, which flip in through
#   `perspective True` with `matrixtransform RotateMatrix(...)`).
# - `blur`: drawn through an extra render-to-texture step (FLATTEN) for a
#   shader the legacy renderer doesn't have, so the blur never shows, yet the
#   render-to-texture is redone whenever the Transform renders again (every
#   frame while it animates: ~40 ms per full-screen Transform on the Vita).
#
# - `matrixcolor`: a shader too, so the legacy renderer ignored it (the test
#   game's locked characters, meant as greyscale cards, showed in full colour).
#
# Under the legacy renderer `perspective` and `blur` are dropped from the
# Transform's state after each state update: content shows, a 3D rotation
# draws as its flat 2D projection (a squash instead of a flip with depth), and
# there is no extra render-to-texture. `matrixcolor` on an image (or an image
# name that resolves to one) is applied on the CPU with im.MatrixColor, which
# the legacy renderer can draw; on anything else it's still ignored. The
# conversion assumes the matrix leaves alpha alone (saturation, brightness,
# tint, ...): gl2 applies it to premultiplied colour, so the alpha column
# becomes a constant offset. An animated matrixcolor is recoloured at most
# every 0.3 s per Transform (a colour fade becomes a few steps).
#
# ImageDissolve (and the other im.* functions) under the legacy renderer
# take a string as a filename, where gl2 (a Transform) also takes an image
# name: the test game's ImageDissolve("whiteblack", ...) for the image
# images/MENUS/whiteblack.jpg found no file, and the wipe showed black
# with an error. A string without an extension that isn't a file is looked
# up as an image name when first loaded.
#
# Under gl2 on the Vita (experimental, see vita_gl2.rpy) only `blur` is
# dropped: gl2's blur shader blurs by sampling mipmaps, which the Vita's
# textures don't have (gl2texture.pyx), so it showed nothing, yet each
# blurred Transform still cost a render-to-texture and an expensive shader
# pass every frame (the test game's character menu: 8 blurred full-screen card layers,
# ~205 ms per frame). gl2 draws perspective and matrixcolor itself.
#
# Active on the Vita; VITA_LEGACY_TRANSFORMS_TEST=1 turns it on elsewhere
# (desktop legacy renderer: config.gl2 = False, RENPY_RENDERER=gl).

init -1500 python:

    def _vita_install_legacy_transforms():
        import os
        import time

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_LEGACY_TRANSFORMS_TEST")):
            return

        transform = renpy.display.transform
        Transform = transform.Transform

        if getattr(Transform, "_vita_legacy_transforms", False):
            return

        def legacy_state(self):
            draw = renpy.display.draw
            if draw is None:
                return
            module = type(draw).__module__
            if module.startswith("renpy.gl."):
                state = self.state
                if state.perspective:
                    state.perspective = False
                if state.blur:
                    state.blur = None
            elif module.startswith("renpy.gl2.") and getattr(renpy, "vita", False):
                if self.state.blur:
                    self.state.blur = None

        def wrap(cls):
            original = cls.__dict__["update_state"]

            def update_state(self):
                rv = original(self)
                legacy_state(self)
                return rv

            cls.update_state = update_state

        # ATLTransform has its own update_state.
        for cls in (Transform, transform.ATLTransform):
            if "update_state" in cls.__dict__:
                wrap(cls)

        im = renpy.display.im

        # (image, matrix values) -> im.MatrixColor, kept small.
        recolored = { }

        def image_of(d):
            if isinstance(d, im.ImageBase):
                return d
            if isinstance(d, renpy.display.image.ImageReference):
                try:
                    d.find_target()
                except Exception:
                    return None
                if isinstance(d.target, im.ImageBase):
                    return d.target
            return None

        def as_im_matrix(m):
            # gl2: (R', G', B', A') = M * premultiplied (R, G, B, A).
            # im.MatrixColor works on straight colour, where the alpha
            # column of an alpha-preserving matrix is a constant offset.
            return [
                m.xdx, m.xdy, m.xdz, 0.0, m.xdw,
                m.ydx, m.ydy, m.ydz, 0.0, m.ydw,
                m.zdx, m.zdy, m.zdz, 0.0, m.zdw,
                m.wdx, m.wdy, m.wdz, m.wdw, 0.0,
                ]

        def legacy():
            draw = renpy.display.draw
            return draw is not None and type(draw).__module__.startswith("renpy.gl.")

        def replacement_for(t):
            """
            The im.MatrixColor to draw instead of `t`'s child, or None.
            """

            mc = t.state.matrixcolor
            child = t.child

            if not mc or child is None or not legacy():
                return None

            try:
                image = image_of(child)
                if image is None:
                    return None

                m = mc(None, 1.0) if callable(mc) else mc
                values = as_im_matrix(m)
                key = (image, tuple(values))

                rv = recolored.get(key)

                if rv is None:
                    # An animated matrixcolor would need a CPU recolouring
                    # every frame: make a new one at most every 0.3 s per
                    # Transform, keep showing the last one in between, and
                    # make sure it renders again for the final colour.
                    now = time.time()
                    last = getattr(t, "_vita_mc_last", None)
                    wait = 0.3 - (now - getattr(t, "_vita_mc_time", 0))

                    if last is not None and wait > 0:
                        renpy.display.render.redraw(t, wait)
                        return last

                    if len(recolored) > 200:
                        recolored.clear()
                    rv = recolored[key] = im.MatrixColor(image, values)
                    t._vita_mc_time = now

                t._vita_mc_last = rv
                return rv
            except Exception:
                return None

        original_render = Transform.render

        def render(self, width, height, st, at):
            replacement = replacement_for(self)
            if replacement is None:
                return original_render(self, width, height, st, at)

            child = self.child
            self.child = replacement
            try:
                return original_render(self, width, height, st, at)
            finally:
                self.child = child

        original_visit = Transform.visit

        def visit(self):
            # Prediction walks displayables through visit(), so this lets it
            # preload the recoloured image instead of the original.
            replacement = replacement_for(self)
            if replacement is None:
                return original_visit(self)
            return [ replacement ]

        Transform.visit = visit
        Transform.render = render

        Transform._vita_legacy_transforms = True

        im = renpy.display.im
        original_image = im.image

        class NamedImage(im.ImageBase):
            # An image name given where a filename was expected: resolved to
            # the image's own im.Image (or other ImageBase) when needed.
            def __init__(self, name, **properties):
                super(NamedImage, self).__init__(name, **properties)
                self.name = name

            def target(self):
                ref = renpy.display.image.ImageReference(tuple(self.name.split()))
                ref.find_target()
                return original_image(ref.target)

            def load(self):
                return im.cache.get(self.target())

            def get_hash(self):
                return self.target().get_hash()

            def get_oversample(self):
                return self.target().get_oversample()

            def predict_files(self):
                return self.target().predict_files()

        # Pickled by name, like Ren'Py's own image classes (a class local to
        # this function couldn't be saved).
        NamedImage.__module__ = "renpy.display.im"
        NamedImage.__name__ = NamedImage.__qualname__ = "_VitaNamedImage"
        im._VitaNamedImage = NamedImage

        def image(arg, loose=False, **properties):
            if isinstance(arg, str) and "." not in arg and not renpy.loader.loadable(arg):
                return NamedImage(arg, **properties)
            return original_image(arg, loose=loose, **properties)

        im.image = image

    _vita_install_legacy_transforms()
