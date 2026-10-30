# RenPy-Vita-8: Ren'Py → PS Vita pipeline

This fork of [Grimiku's RenPy-Vita-8](https://github.com/Grimiku/RenPy-Vita-8) adds a pipeline that turns a Ren'Py game's PC release into a complete PS Vita app (a VPK), and makes the game run well on the Vita.

It has two parts:

- **[`pipeline/`](pipeline/README.md):** scripts that build the app. In one run they:
  - extract the game's files
  - re-encode its movies
  - shrink its images and fonts
  - add the Vita compatibility and speed-up hooks
  - write the VPK, or upload the app straight to a Vita
- **The port itself, on the `vita-perf` branches** of this repo and its `renpy-8` and `pygame_sdl2` submodules:
  - a hardware movie player (two movies at once, gapless loops)
  - faster image loading and boot
  - fixes for crashes and hangs

  `pipeline/vita_port_build.py` builds the eboot from these. The patched libraries in `pipeline/patches/` add more fixes and speed; CPython's timed-wait fix alone makes Python about 3× faster.

Only the PVR build of the port is supported, not VitaGL.

## Compared with the original

Measured on a PS Vita with one large commercial Ren'Py 8.3 game: ~140,000 script statements, 13,000 images and 449 movies. The original is the RenPy-Vita-8 v0.2.1 PVR release, installed the usual way.

| | Original v0.2.1 | This fork |
|---|---|---|
| **Getting a game onto the Vita** | | |
| Making the app | By hand: build a VPK with SonicMastr's distribution tool, install it, then unpack the port's zip over it | One command turns the PC release into a VPK, or uploads it to the Vita |
| Building the eboot from source | Rough notes; the libraries are set up by hand | Scripted, with pinned versions and checksums (~7 min), then one build command |
| Memory | 180 MB heap: the test game ran out ("Out of memory" in place of images) | 250 MB heap |
| Loading screen | Ren'Py's presplash crashes the port, so the screen stays blank | Loading image with a progress bar |
| **Speed** | | |
| Boot | ~170 s, then out of memory (first episode only) | ~35 s (whole game) |
| Advancing dialogue | ~400–550 ms per line | ~100 ms per line |
| Python | Timed waits return at once, so idle threads spin and fight for the interpreter | Fixed: ~3× faster (busy loop 94k → 277k iterations/s) |
| Image cache | Off: the port's low-memory mode left its size at 0, so nothing was preloaded | 48 MB, with images preloaded in the background |
| Full-screen JPEG decode | ~480 ms | ~45 ms on the main thread; preloading uses the Vita's hardware JPEG decoder |
| First frame of a transition | ~200 ms | 12–41 ms |
| Opening a large menu (21 full-screen images) | 6.5 s | 1.75 s |
| New glyphs (fonts read from the card through Python) | 5–11 ms each | 0.35 ms each |
| **Movies** | | |
| Formats | MP4 (H.264/AAC) only; games' WebM movies don't play | Movies converted to MP4 automatically |
| Playback, 960×544 | 7.8 fps (each frame copied to the GPU by the CPU) | Full frame rate, 60 fps cutscenes included (decoded straight into GPU memory) |
| `Movie` images in scenes | Never play | Play, and loop without a gap |
| Two movies at once (e.g. a dissolve between them) | One player only | Both play, 60 fps through the dissolve |
| **Fixes** | | |
| Audio files that start at time 0 | Game and music freeze ~20 s each time one starts (FFmpeg built with `-ffast-math`) | Fixed |
| Quitting the game | Crash (error C2-12828-1) | Clean exit |
| Dimming and standby during play | Blocked | Allowed (OLED screens) |
| Shader effects (masks, colour matrices, perspective) | Drawn black or not at all | Emulated on the CPU, or drawn by the GPU with the experimental gl2 renderer |
| D-pad navigation | Stuck on full-screen masked buttons; one press could move twice | Works |
| Touch | Phone layout not used (tiny desktop UI) | Phone layout and larger quick-menu buttons |

Details and the measurements behind each row are in [pipeline/README.md](pipeline/README.md) and the commit messages.

## Getting started

```
git clone --recurse-submodules -b vita-perf https://github.com/LemoneCello/RenPy-Vita-8.git
```

Then follow [pipeline/README.md](pipeline/README.md). In outline:
1. Set up a workspace folder next to this repo, holding your game's PC release.
2. Build the toolchain and the eboot once.
3. Make a settings file for the game.
4. Run `uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml`.

Only use it on games you own, and don't distribute the processed game files.

## Credits

- [Grimiku's RenPy-Vita-8](https://github.com/Grimiku/RenPy-Vita-8): Ren'Py 8 / Python 3.11 on the Vita, which this builds on.
- [SonicMastr's renpy-vita](https://github.com/SonicMastr/renpy-vita): the original Ren'Py 7 port.
- [Ren'Py](https://www.renpy.org/).

MIT licensed, as upstream (see `LICENSE`); the bundled projects keep their own licences.
