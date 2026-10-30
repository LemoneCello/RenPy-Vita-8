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
