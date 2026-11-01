# Faster render-to-texture (RTT) for Ren'Py's legacy gles renderer on the
# Vita. A transition (Dissolve and the like) renders the old and the new
# scene into textures on its first frame; on the Vita that frame took ~200 ms.
#
# 1. Copy. The port only allows "copy RTT": draw to the screen, then copy the
#    pixels into the texture with glCopyTexSubImage2D (its gldraw.pyx turns
#    FBO RTT off on the Vita, and Ren'Py's own FBO RTT would copy too). That
#    copy costs ~30-55 ms per 960x540 texture. Here the drawing goes straight
#    into the destination texture through a framebuffer object instead. The
#    renderer's RTT object is a public Python attribute with a plain Python
#    interface, so it is wrapped, not replaced: the stock CopyRtt still sets
#    up the viewport and projection (cdef methods Python can't call) and
#    calls the draw function, which then lands in our framebuffer. Its
#    closing copy is pointed at a 1x1 texture, which GL rejects as too large
#    (GL_INVALID_VALUE, nothing is copied); the error is cleared right after.
#
# 2. Allocation. With non-power-of-two textures (preferences.gl_npot, on for
#    the Vita) Ren'Py never reuses a texture, so every RTT started with
#    glTexImage2D(NULL): ~28 ms at 960x540. The first render into a new
#    texture also waits another ~25 ms for the GPU.
#
# 3. Driver memory. The Vita's GPU driver keeps a large allocation for every
#    texture that has been a framebuffer attachment: after 7 or so, attaching
#    another fails with GL_OUT_OF_MEMORY and the framebuffer is unsupported,
#    so transitions drew black (seen as the screen going black for the length
#    of each transition).
#
# So a fixed set of screen-sized textures is created once per GL generation
# (at the first interaction, each rendered into once) and kept: RTTs at that
# size get one of them when one is free and draw straight into it. Everything else (other sizes, all targets in use, or a framebuffer that
# isn't complete) uses the stock copy into a normal texture. Image textures
# are left alone: re-uploading an image into a reused non-power-of-two
# texture is slower on the Vita than a new one.
#
# Measured on the Vita: a dissolve's first frame ~200 ms -> 22-52 ms while a
# target is free.
#
# 4. Copies. RTTs beyond the free targets (and of other sizes) still use the
#    stock copy, but into a texture of the same size kept in a pool and
#    reused, instead of a new one each time (a copy never attaches its texture to a framebuffer, so
#    the driver limit in 3 doesn't apply). Effects that render to texture
#    every frame need this: four of Doki Doki Literature Club!'s animated
#    image-dissolve masks make ~20 RTTs a frame, and creating and freeing a
#    960x540 texture for each of the ~16 copies most likely crashed the
#    Vita's GPU. The pool grows to max_copies; textures unused for
#    copy_idle seconds are freed at the next interaction.
#
# Active on the Vita with copy RTT; VITA_FBO_RTT_TEST=1 turns it on elsewhere
# (desktop: config.gl2 = False, RENPY_RENDERER=gl, RENPY_GL_RTT=copy).

init -1500 python:

    def _vita_install_fbo_rtt(count, max_copies=24, copy_idle=10.0):
        import os
        import sys
        import time

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_FBO_RTT_TEST")):
            return

        try:
            import renpy.gl.gltexture as gltexture
            import renpy.gl.glrtt_copy as glrtt_copy
            import renpy.uguu as gl
        except Exception:
            return

        if getattr(gltexture, "_vita_fbo_rtt", False):
            return

        gltexture._vita_fbo_rtt = True

        def log(msg):
            renpy.display.log.write("vita_fbo_rtt: " + msg)

        state = {
            # Inside texture_grid_from_drawing (an RTT is allocating).
            "rtt" : 0,
            # Creating the render targets.
            "creating" : False,
            # The GL generation the targets were made for.
            "generation" : None,
            # (width, height) of the targets.
            "size" : None,
            # Texture numbers of the targets.
            "numbers" : set(),
            # Logged a fallback already.
            "warned" : False,
            # References a free target has (set when the targets are made).
            "free_refs" : 0,
            }

        # The targets, held here for as long as their GL generation lasts. A
        # target is free when nothing but this list refers to it. (Ren'Py's
        # own free lists rely on Texture.__del__ putting the texture back,
        # but Python runs __del__ only once per object, so a reused texture
        # would be dropped the second time, leaking its GL texture.)
        targets = [ ]
        state["targets"] = targets

        # Pooled textures for copy RTTs (see 4 above), with the time each was
        # last handed out. Free on the same terms as the targets.
        copies = [ ]
        copy_used = { }
        state["copies"] = copies

        # For diagnostics.
        gltexture._vita_fbo_rtt_state = state

        def refs(t):
            return sys.getrefcount(t)

        original_alloc_texture = gltexture.alloc_texture
        original_texture_grid_from_drawing = gltexture.texture_grid_from_drawing
        original_dealloc_textures = gltexture.dealloc_textures

        def alloc_texture(width, height):
            if not state["rtt"]:
                return original_alloc_texture(width, height)

            if state["creating"]:
                state["size"] = (width, height)
                rv = original_alloc_texture(width, height)
                rv.free_list = None
                targets.append(rv)
                return rv

            if (width, height) == state["size"]:
                for t in targets:
                    if refs(t) <= state["free_refs"] and t.number in state["numbers"] and t.generation == gltexture.generation:
                        return t

            if state["free_refs"]:
                for t in copies:
                    if t.width == width and t.height == height and refs(t) <= state["free_refs"] and t.generation == gltexture.generation:
                        copy_used[id(t)] = time.time()
                        return t

                if len(copies) < max_copies:
                    rv = original_alloc_texture(width, height)
                    # Ours: never handed back to Ren'Py's free lists.
                    rv.free_list = None
                    copies.append(rv)
                    copy_used[id(rv)] = time.time()
                    return rv

            return original_alloc_texture(width, height)

        def texture_grid_from_drawing(*args, **kwargs):
            state["rtt"] += 1
            try:
                return original_texture_grid_from_drawing(*args, **kwargs)
            finally:
                state["rtt"] -= 1

        def dealloc_textures(*args, **kwargs):
            for t in targets + copies:
                t.deallocate()
            del targets[:]
            del copies[:]
            copy_used.clear()
            state["numbers"].clear()
            state["generation"] = None

            return original_dealloc_textures(*args, **kwargs)

        class DirectRtt(object):
            """
            Wraps a CopyRtt so that it renders into our target textures
            through a framebuffer object.
            """

            _vita_fbo_rtt = True

            def __init__(self, inner):
                self.inner = inner

                buf = gl.IntBuffer([ 0 ])
                gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, buf)
                self.root = buf[0]

                buf = gl.IntBuffer([ 0 ])
                gl.glGenFramebuffers(1, buf)
                self.fbo = buf[0]

                buf = gl.IntBuffer([ 0 ])
                gl.glGenTextures(1, buf)
                self.dummy = buf[0]
                gl.glBindTexture(gl.GL_TEXTURE_2D, self.dummy)
                gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, 1, 1, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)

                self.alive = True

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def deinit(self):
                gl.glDeleteFramebuffers(1, gl.IntBuffer([ self.fbo ]))
                gl.glDeleteTextures(1, gl.IntBuffer([ self.dummy ]))
                self.alive = False

                return self.inner.deinit()

            def render(self, environ, texture, x, y, w, h, draw_func):
                if state["creating"]:
                    state["numbers"].add(texture)
                elif not self.alive or texture not in state["numbers"]:
                    return self.inner.render(environ, texture, x, y, w, h, draw_func)

                gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
                gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, texture, 0)

                status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)

                if status != gl.GL_FRAMEBUFFER_COMPLETE:
                    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, 0, 0)
                    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.root)
                    error = gl.glGetError()

                    # Never use this texture as a target again.
                    state["numbers"].discard(texture)

                    if not state["warned"]:
                        state["warned"] = True
                        log("framebuffer status %x (GL error %x), using the stock copy" % (status, error))

                    return self.inner.render(environ, texture, x, y, w, h, draw_func)

                try:
                    self.inner.render(environ, self.dummy, x, y, w, h, draw_func)
                finally:
                    gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, gl.GL_TEXTURE_2D, 0, 0)
                    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.root)

                    # The rejected copy into the 1x1 texture.
                    gl.glGetError()

        def release_idle_copies():
            now = time.time()
            for t in list(copies):
                if now - copy_used.get(id(t), 0) > copy_idle and refs(t) <= state["free_refs"] + 1:
                    copies.remove(t)
                    copy_used.pop(id(t), None)
                    t.deallocate()

        def interact():
            if copies:
                release_idle_copies()

            draw = renpy.display.draw
            rtt = getattr(draw, "rtt", None)

            if rtt is None:
                return

            # The renderer creates a new RTT object whenever it initialises
            # GL, so check at each interaction.
            if not getattr(rtt, "_vita_fbo_rtt", False):

                if type(rtt) is not glrtt_copy.CopyRtt:
                    return

                try:
                    draw.rtt = DirectRtt(rtt)
                    log("installed")
                except Exception as e:
                    log("install failed: %r" % (e,))
                    draw.rtt = rtt
                    rtt._vita_fbo_rtt = True
                    return

            if not isinstance(draw.rtt, DirectRtt):
                return

            # Create the targets for this GL generation.
            if state["generation"] == gltexture.generation:
                return

            state["generation"] = gltexture.generation
            state["creating"] = True

            try:
                what = renpy.display.render.Render(config.screen_width, config.screen_height)
                grids = [ draw.render_to_texture(what, False) for _i in range(count) ]
                del grids

                # How many references an object only this list refers to
                # has, counted the way alloc_texture counts.
                probe = [ object() ]
                for t in probe:
                    state["free_refs"] = refs(t)

                busy = 0
                for t in targets:
                    if refs(t) > state["free_refs"]:
                        busy += 1
                if busy:
                    log("%d new render targets are still referenced" % busy)

                log("%d render targets of %dx%d" % (len(state["numbers"]), state["size"][0], state["size"][1]))
            except Exception as e:
                log("creating render targets failed: %r" % (e,))
            finally:
                state["creating"] = False

        gltexture.alloc_texture = alloc_texture
        gltexture.texture_grid_from_drawing = texture_grid_from_drawing
        gltexture.dealloc_textures = dealloc_textures
        config.interact_callbacks.append(interact)

    _vita_install_fbo_rtt(count=4)
