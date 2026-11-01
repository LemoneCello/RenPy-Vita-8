# Doki Doki Literature Club! 1.1.1 on the Vita: a game-specific patch, added
# to the build by the game's settings (rpy_extra = [".../games/ddlc"]).
#
# Monika's room (act 3) shows four of the game's AnimatedMask displayables
# (mask_test to mask_test4: the animated views in its windows) at 320x180.
# Each draws an image dissolve of three full-screen renders, rebuilt every
# frame, and the Vita's renderer turns each into a texture: ~12 full-screen
# render-to-textures a frame for something shown at a sixteenth of the
# screen, about one frame a second on the Vita. Here those four render at a
# quarter of the size and are drawn back at full size, so their textures are
# about what's on screen (240x135 on the Vita), and the two parts that never
# change (the colour and an empty layer) keep their textures, so only the
# moving mask is rendered again each frame. The slide of the mask keeps its
# speed on screen; the glow's pulse is unchanged. Other AnimatedMasks (the
# full-screen veins in act 2) are left alone.

init 501 python:

    def _vita_ddlc_room_masks(names=("mask_test", "mask_test2", "mask_test3", "mask_test4"), scale=4):
        import math

        cls = globals().get("AnimatedMask")
        if cls is None or getattr(cls, "_vita_small_render", False):
            return

        original_render = cls.render

        def small_render(self, width, height, st, at):
            s = getattr(self, "_vita_scale", 1)
            if s <= 1:
                return original_render(self, width, height, st, at)

            # The masks are images: zoomed. The child is rendered at full
            # size and shrunk, as it may fill whatever size it's given (a
            # Solid colour does).
            small = self.__dict__.get("_vita_small")
            if small is None:
                z = 1.0 / s
                small = (Transform(self.mask, zoom=z), Transform(self.maskb, zoom=z))
                self._vita_small = small
            mask, maskb = small

            sw = width / s
            sh = height / s

            mr = renpy.render(mask, sw, sh, st, at)
            mw, mh = mr.get_size()

            # The colour (a Solid: it fills the size it's given) and the empty
            # layer never change, but render anew each frame, and each would
            # be copied into a texture again. Kept here with their textures
            # while the size stays the same; only the moving mask is redone.
            static = self.__dict__.get("_vita_static")
            solid = isinstance(self.child, renpy.display.imagelike.Solid)
            if (not solid or static is None or static[0] != (width, height)
                    or getattr(static[1], "killed", False) or getattr(static[2], "killed", False)):
                full = renpy.render(self.child, width, height, st, at)
                fw, fh = full.get_size()
                cr = renpy.Render(fw / s, fh / s)
                cr.zoom(1.0 / s, 1.0 / s)
                cr.blit(full, (0, 0))
                w = min(fw / s, mw)
                h = min(fh / s, mh)
                nr = renpy.Render(w, h)
                static = ((width, height), cr, nr)
                if solid:
                    self._vita_static = static
            _size, cr, nr = static

            mb = renpy.Render(sw, sh)

            # As the game's render, in quarter-size pixels.
            if self.moving:
                mb.place(mask, ((-st * 50.0 / s) % (sw * 2)) - (sw * 2), 0)
                mb.place(maskb, -sw / 2, 0)
            else:
                mb.place(mask, 0, 0)
                mb.place(maskb, 0, 0)

            w, h = nr.get_size()

            rv = renpy.Render(w, h)
            rv.operation = renpy.display.render.IMAGEDISSOLVE
            rv.operation_alpha = 1.0
            rv.operation_complete = self.oc + math.pow(math.sin(st * self.speed / 8), 64 * self.frequency) * self.amount
            rv.operation_parameter = self.op
            rv.blit(mb, (0, 0), focus=False, main=False)
            rv.blit(nr, (0, 0), focus=False, main=False)
            rv.blit(cr, (0, 0))

            big = renpy.Render(w * s, h * s)
            big.zoom(s, s)
            big.blit(rv, (0, 0))

            renpy.redraw(self, 0)
            return big

        cls.render = small_render
        cls._vita_small_render = True

        images = renpy.display.image.images
        for name in names:
            d = images.get((name,))
            if isinstance(d, cls):
                d._vita_scale = scale
            else:
                renpy.display.log.write("vita_ddlc_room_masks: %s isn't an AnimatedMask (%r)" % (name, d))

    _vita_ddlc_room_masks()
