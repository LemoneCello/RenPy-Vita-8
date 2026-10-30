# The Vita port runs Ren'Py's legacy "gles" renderer, which has no shaders, so
# the shader-based AlphaMask draws black. Replace it with a static mask applied
# once on the CPU when the image loads. Transforms on the child (e.g. hover
# zoom) are dropped, since animating the child under a mask needs the shader.
#
# VITA_ALPHAMASK_TEST=1 enables this on desktop for comparison screenshots.

init -1500 python:

    def _vita_install_alphamask():
        import os

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_ALPHAMASK_TEST")):
            return

        # The gl2 renderers (gles2, ...) have the shader.
        if os.environ.get("RENPY_RENDERER", "").endswith("2"):
            return

        im = renpy.display.im

        # alpha_munge reads the mask's red channel; copy alpha there first.
        alpha_to_red = [0, 0, 0, 1, 0,
                        0, 0, 0, 1, 0,
                        0, 0, 0, 1, 0,
                        0, 0, 0, 1, 0]

        class MaskedImage(im.ImageBase):
            """Child cropped (centred) to the mask's size, alpha taken from the mask."""

            def __init__(self, base, mask, **properties):
                super(MaskedImage, self).__init__(base, mask, **properties)
                self.base = base
                self.mask = im.MatrixColor(mask, alpha_to_red)
                # Keep the base's oversample, e.g. 0.5 from vita_oversample.rpy.
                self.oversample = base.get_oversample()

            def get_hash(self):
                return self.base.get_hash() + self.mask.get_hash()

            def load(self):
                basesurf = im.cache.get(self.base)
                masksurf = im.cache.get(self.mask)
                (bw, bh), (mw, mh) = basesurf.get_size(), masksurf.get_size()
                w, h = min(bw, mw), min(bh, mh)
                base = basesurf.subsurface(((bw - w) // 2, (bh - h) // 2, w, h))
                mask = masksurf.subsurface(((mw - w) // 2, (mh - h) // 2, w, h))
                rv = renpy.display.pgrender.copy_surface(base)
                renpy.display.module.alpha_munge(mask, rv, im.identity)
                return rv

            def predict_files(self):
                return self.base.predict_files() + self.mask.predict_files()

        def as_image(d):
            d = renpy.easy.displayable(d)
            while isinstance(d, renpy.display.transform.Transform) and d.child is not None:
                d = d.child
            if isinstance(d, renpy.display.image.ImageReference):
                name = d.name
                d = renpy.display.image.images.get(name if isinstance(name, tuple) else tuple(name.split()))
            return d if isinstance(d, im.ImageBase) else None

        def AlphaMask(child, mask, invert=False, **properties):
            base = as_image(child)
            mask_image = as_image(mask)
            if base is None or mask_image is None or invert:
                return renpy.easy.displayable(child)
            return MaskedImage(base, mask_image, **properties)

        store.AlphaMask = AlphaMask

    _vita_install_alphamask()
