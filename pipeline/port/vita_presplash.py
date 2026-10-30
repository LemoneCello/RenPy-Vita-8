# GLES presplash for the Ren'Py PS Vita port (RenPy-Vita-8, PVR build).
#
# Ren'Py draws its presplash on an SDL window surface, which this port can't
# provide: SDL's GXM renderer fails to start next to the PVR GLES driver, and
# the PVR video driver has no plain framebuffer. So games that ship
# presplash images crash at launch, and games without them sit on a black
# screen while the script loads.
#
# This replaces renpy.display.presplash's start/pump_window/end with versions
# that draw the game's presplash images with OpenGL ES 2. The window is opened
# with pygame_sdl2.display.set_mode, so Ren'Py's renderer reuses it later; it
# must not be destroyed (the port can't create a second GL window).
#
# pygame_sdl2 on the port can't hand image pixels to GL (no get_view, no
# ctypes), so vita_presplash_build.py converts the images at build time into
# vita_presplash_background.bin (+ _foreground.bin, revealed left to right as
# a progress bar), stored next to renpy.py. Without them nothing is drawn.
# Ren'Py's own presplash is never used: on the Vita it can only crash, so the
# game's presplash_*.png must not be in game/.
#
# Installed from renpy.py before bootstrap:
#
#     import vita_presplash
#     vita_presplash.install(renpy_base)

import os
import time
import traceback

# Seconds between redraws while loading. Each redraw waits for vsync.
REDRAW_INTERVAL = 0.25

# Written next to renpy.py; overwritten on each launch.
LOG_NAME = "vita_presplash.txt"

# Seconds from start to each pump on the last boot, next to renpy.py. The
# progress bar uses it to advance by time rather than by step; Ren'Py's step
# count is far from even (one long "Loading script" stage).
TIMING_NAME = "vita_presplash_timing.json"

# Converted images, next to renpy.py. Format: b"VPS1", width and height
# (little-endian u32), then zlib-compressed RGBA rows, top row first.
BACKGROUND_NAME = "vita_presplash_background.bin"
FOREGROUND_NAME = "vita_presplash_foreground.bin"
MAGIC = b"VPS1"

VERTEX_SHADER = b"""
attribute vec2 a_position;
attribute vec2 a_texcoord;
varying vec2 v_texcoord;

void main() {
    v_texcoord = a_texcoord;
    gl_Position = vec4(a_position, 0.0, 1.0);
}
"""

FRAGMENT_SHADER = b"""
#ifdef GL_ES
precision mediump float;
#endif

uniform sampler2D u_texture;
varying vec2 v_texcoord;

void main() {
    gl_FragColor = texture2D(u_texture, v_texcoord);
}
"""


class Log(object):

    def __init__(self, path):
        self.path = path
        self.start = time.time()

        try:
            with open(path, "w") as f:
                f.write("vita_presplash\n")
        except Exception:
            self.path = None

    def __call__(self, msg):
        if self.path is None:
            return

        try:
            with open(self.path, "a") as f:
                f.write("%7.2f %s\n" % (time.time() - self.start, msg))
        except Exception:
            pass


def find_images(basedir):
    """
    Returns (background, foreground) filenames, foreground None for a single
    presplash image, or None if there are no converted images.
    """

    background = os.path.join(basedir, BACKGROUND_NAME)
    foreground = os.path.join(basedir, FOREGROUND_NAME)

    if not os.path.exists(background):
        return None

    if not os.path.exists(foreground):
        foreground = None

    return background, foreground


def read_image(fn):
    """
    Returns (width, height, rgba_bytes) from a converted image.
    """

    import struct
    import zlib

    with open(fn, "rb") as f:
        data = f.read()

    if data[:4] != MAGIC:
        raise Exception("%s: not a vita_presplash image" % fn)

    w, h = struct.unpack_from("<II", data, 4)
    pixels = zlib.decompress(data[12:])

    if len(pixels) != w * h * 4:
        raise Exception("%s: expected %d bytes, got %d" % (fn, w * h * 4, len(pixels)))

    return w, h, pixels


class GLPresplash(object):
    """
    Draws the presplash with GLES 2 on pygame_sdl2's main window.
    """

    def __init__(self, background, foreground, log):
        self.background_fn = background
        self.foreground_fn = foreground
        self.log = log

        self.window = None
        self.program = None
        self.textures = [ ]

        # (texture, width, height) in window pixels.
        self.background = None
        self.foreground = None

    def open(self):
        import pygame_sdl2

        pygame_sdl2.display.init()

        # The port's renderer asks for ES 2.0 too (the SGX543 has no ES 3).
        pygame_sdl2.display.gl_set_attribute(pygame_sdl2.GL_CONTEXT_PROFILE_MASK, pygame_sdl2.GL_CONTEXT_PROFILE_ES)
        pygame_sdl2.display.gl_set_attribute(pygame_sdl2.GL_CONTEXT_MAJOR_VERSION, 2)
        pygame_sdl2.display.gl_set_attribute(pygame_sdl2.GL_CONTEXT_MINOR_VERSION, 0)

        # The same flags Ren'Py's gles renderer uses in fullscreen mode.
        flags = pygame_sdl2.WINDOW_FULLSCREEN_DESKTOP | pygame_sdl2.OPENGL | pygame_sdl2.DOUBLEBUF
        pygame_sdl2.display.set_mode((0, 0), flags)

        self.window = pygame_sdl2.display.get_window()
        self.width, self.height = self.window.get_size()

        import renpy.uguu.gl
        renpy.uguu.gl.load()

    def start(self):
        import renpy.uguu as gl

        self.open()
        self.log("window %dx%d, %r" % (self.width, self.height, gl.glGetString(gl.GL_RENDERER)))

        self.program = self.make_program()
        self.background = self.load_texture(self.background_fn)

        if self.foreground_fn:
            self.foreground = self.load_texture(self.foreground_fn)

        self.draw(0.0)
        self.log("first frame drawn")

    def compile_shader(self, kind, source):
        import renpy.uguu as gl

        shader = gl.glCreateShader(kind)
        gl.glShaderSource(shader, 1, gl.BytesListBuffer([ source ]), None)
        gl.glCompileShader(shader)

        status = gl.IntBuffer([ 0 ])
        gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS, status)

        if status[0] != gl.GL_TRUE:
            log = gl.BytesBuffer(1024)
            gl.glGetShaderInfoLog(shader, 1024, None, log)
            raise Exception("shader compile failed: %r" % (log.get(),))

        return shader

    def make_program(self):
        import renpy.uguu as gl

        vertex = self.compile_shader(gl.GL_VERTEX_SHADER, VERTEX_SHADER)
        fragment = self.compile_shader(gl.GL_FRAGMENT_SHADER, FRAGMENT_SHADER)

        program = gl.glCreateProgram()
        gl.glAttachShader(program, vertex)
        gl.glAttachShader(program, fragment)
        gl.glBindAttribLocation(program, 0, b"a_position")
        gl.glBindAttribLocation(program, 1, b"a_texcoord")
        gl.glLinkProgram(program)

        status = gl.IntBuffer([ 0 ])
        gl.glGetProgramiv(program, gl.GL_LINK_STATUS, status)

        if status[0] != gl.GL_TRUE:
            raise Exception("shader link failed")

        gl.glDeleteShader(vertex)
        gl.glDeleteShader(fragment)

        return program

    def load_texture(self, fn):
        """
        Uploads a converted image as an RGBA texture. Returns (texture, width,
        height); the build step already scaled it to fit the screen.
        """

        import renpy.uguu as gl

        w, h, pixels = read_image(fn)

        texture = gl.IntBuffer([ 0 ])
        gl.glGenTextures(1, texture)
        texture = texture[0]
        self.textures.append(texture)

        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)

        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, gl.ptr(pixels))

        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        self.log("loaded %s (%dx%d)" % (os.path.basename(fn), w, h))

        return texture, w, h

    def quad(self, image, fraction):
        """
        Draws the left `fraction` of image, centred in the window.
        """

        import renpy.uguu as gl

        texture, w, h = image

        x0 = (self.width - w) / 2.0
        y0 = (self.height - h) / 2.0
        x1 = x0 + w * fraction
        y1 = y0 + h

        def nx(x):
            return x / self.width * 2.0 - 1.0

        def ny(y):
            return 1.0 - y / self.height * 2.0

        positions = gl.FloatBuffer([
            nx(x0), ny(y0),
            nx(x1), ny(y0),
            nx(x0), ny(y1),
            nx(x1), ny(y1),
            ])

        texcoords = gl.FloatBuffer([
            0.0, 0.0,
            fraction, 0.0,
            0.0, 1.0,
            fraction, 1.0,
            ])

        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 0, positions)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 0, texcoords)
        gl.glDrawArrays(gl.GL_TRIANGLE_STRIP, 0, 4)

    def draw(self, done):
        import pygame_sdl2
        import renpy.uguu as gl

        done = max(0.0, min(1.0, done))

        gl.glViewport(0, 0, self.width, self.height)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glUseProgram(self.program)
        gl.glUniform1i(gl.glGetUniformLocation(self.program, b"u_texture"), 0)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glEnableVertexAttribArray(0)
        gl.glEnableVertexAttribArray(1)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        self.quad(self.background, 1.0)

        if self.foreground is not None and done > 0:
            self.quad(self.foreground, done)

        gl.glDisableVertexAttribArray(0)
        gl.glDisableVertexAttribArray(1)

        pygame_sdl2.display.flip()

    def close(self):
        """
        Frees the GL objects, leaving the window (and the last frame) for
        Ren'Py's renderer.
        """

        import renpy.uguu as gl

        gl.glUseProgram(0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glDisable(gl.GL_BLEND)

        if self.textures:
            gl.glDeleteTextures(len(self.textures), gl.IntBuffer(self.textures))
            self.textures = [ ]

        if self.program is not None:
            gl.glDeleteProgram(self.program)
            self.program = None


def install(renpy_base):
    """
    Replaces Ren'Py's presplash with the GL one. Never raises: on any problem
    it logs to LOG_NAME and leaves Ren'Py's own presplash in place (which on
    the Vita only works when the game has no presplash images).
    """

    log = Log(os.path.join(renpy_base, LOG_NAME))

    try:
        _install(renpy_base, log)
    except Exception:
        log(traceback.format_exc())


def _install(renpy_base, log):
    import renpy
    import renpy.display.presplash as presplash
    import pygame_sdl2

    state = { "splash": None, "pumps": 0, "last": 0.0, "times": [ ], "previous": None }

    timing_path = os.path.join(renpy_base, TIMING_NAME)

    def load_timing():
        import json

        try:
            with open(timing_path) as f:
                times = json.load(f)
            if times and times[-1] > 0:
                return times
        except Exception:
            pass

        return None

    def save_timing():
        import json

        try:
            with open(timing_path, "w") as f:
                json.dump([ round(t, 3) for t in state["times"] ], f)
        except Exception:
            log(traceback.format_exc())

    def progress():
        """
        Fraction done after the current pump.
        """

        pumps = state["pumps"]
        previous = state["previous"]

        if previous is not None:
            return previous[min(pumps, len(previous)) - 1] / previous[-1]

        script = renpy.game.script
        if script is None:
            return 0.0

        # Ren'Py's own estimate: one pump per script file, ~23 boot stages.
        return pumps / (len(script.script_files) + 23.0)

    def start(basedir, gamedir):
        presplash.start_time = time.time()

        images = find_images(renpy_base)

        if images is None:
            log("no %s; no presplash" % (BACKGROUND_NAME,))
            return

        state["previous"] = load_timing()
        log("timing from last boot: %s" % ("%d pumps, %.1f s" % (len(state["previous"]), state["previous"][-1]) if state["previous"] else "none"))

        try:
            splash = GLPresplash(images[0], images[1], log)
            splash.start()
            state["splash"] = splash
            state["last"] = time.time()
        except Exception:
            # The window may exist at this point; Ren'Py's renderer reuses it.
            log(traceback.format_exc())

    def pump_window():
        state["pumps"] += 1
        state["times"].append(time.time() - presplash.start_time)

        splash = state["splash"]
        if splash is None:
            return

        now = time.time()
        if now - state["last"] < REDRAW_INTERVAL:
            return

        state["last"] = now

        done = progress() if splash.foreground is not None else 0.0

        try:
            splash.draw(done)
        except Exception:
            log(traceback.format_exc())
            state["splash"] = None

        for ev in pygame_sdl2.event.get():
            if ev.type == pygame_sdl2.QUIT:
                raise renpy.game.QuitException(relaunch=False, status=0)

    def end():
        presplash.done = True

        splash = state["splash"]
        state["splash"] = None

        if splash is None:
            return

        state["times"].append(time.time() - presplash.start_time)

        # A different pump count means the game or Ren'Py changed; the old
        # timing no longer lines up, so record this boot instead.
        previous = state["previous"]
        if previous is None or len(previous) != len(state["times"]):
            log("pump count changed (%s -> %d)" % (len(previous) if previous else None, len(state["times"])))
        save_timing()

        try:
            splash.draw(1.0)
            splash.close()
            log("handed over to Ren'Py after %d pumps, %.1f s" % (state["pumps"], state["times"][-1]))
        except Exception:
            log(traceback.format_exc())

    presplash.start = start
    presplash.pump_window = pump_window
    presplash.end = end

    log("installed")
