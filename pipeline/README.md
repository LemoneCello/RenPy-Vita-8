# Ren'Py → PS Vita pipeline

Turns a Ren'Py game's PC release into a PS Vita app for this port (RenPy-Vita-8, PVR build), and makes it run well. Each step is a separate script; one command runs them all. It was developed on one large commercial Ren'Py 8.3 game ("the test game" in the code comments): about 140,000 script statements, 13,000 images and 449 movies. It hasn't been tried on others yet, but the fixes are meant to be generic.

On the Vita, that game boots in about 35 s and dialogue advances in about 100 ms. Its 60 fps movies play on the Vita's hardware decoder, including two at once.

**Game files:** the pipeline extracts, converts and repacks the game's own assets. Only use it on games you own, for your own use, and don't distribute what it produces.

## Workspace

The scripts run in a **workspace folder**, which is the current directory. That folder holds:
- this repository
- the games' PC releases
- the build output and toolchain, in `build/`

For example:

```
workspace/
  RenPy-Vita-8/          this repository (pipeline/ is here)
  MyGame-1.0-pc/      a game's PC release: game/, renpy/, lib/
  build/                 everything generated (toolchain, caches, outputs)
```

`pipeline/container/` has a Debian image with everything the scripts need. Start it with `RenPy-Vita-8/pipeline/container/run-container.sh` from the workspace. The container sees only the workspace, mounted at `/work`, and reaches the Vita over Wi-Fi through Docker's normal network. Options:
- `--usb MOUNT` shares the Vita's memory card (see step 4 of "Running the pipeline").
- `--dev` is for working on the pipeline itself. It adds a bigger image (sudo, Claude Code, desktop Ren'Py test runs), the host's network and the Vita's webcam feed. The scripts use [uv](https://docs.astral.sh/uv/) (`uv run script.py`); each script declares its own dependencies.

## Building the eboot

The eboot is built once and is the same for every game. It takes two commands, run from the workspace:

```
uv run RenPy-Vita-8/pipeline/vita_toolchain.py
uv run RenPy-Vita-8/pipeline/vita_port_build.py
```

**`vita_toolchain.py`** sets up everything in `build/toolchain/`, with pinned versions and checksums. A full run took under 7 minutes on 32 cores (longer with fewer; most of it is FFmpeg and CPython); reruns skip finished steps.

| Step | What |
|---|---|
| `vitasdk` | VitaSDK, series 2026.08, from vdpm's bootstrap |
| `packages` | The vdpm libraries the port links. It warns if vdpm serves different versions than the ones the eboot was developed with. |
| `pvr` | PVR_PSP2 v3.9 GLES/EGL headers and stub libraries |
| `hostpy` | Host Python 3.11 with Cython 0.29.37 (not 3.x) |
| `libjpeg` | libjpeg-turbo 3.2.0 with NEON: JPEG decoding ~40% faster |
| `zlib` | zlib-ng 2.2.4 as a drop-in `libz.a`, with NEON: faster PNG decoding |
| `sdl2` | SDL2 2.32.8 with the PVR video driver. Patches: the on-screen keyboard crashed; file reads were 3–4× slow. |
| `sdl2_image` | SDL2_image 2.8.12 with real libjpeg/libpng instead of stb_image. Patches: JPEG straight to RGBA; the Vita's hardware JPEG decoder for background loads. |
| `harfbuzz` | HarfBuzz 14.5.0. Patch: the game hung for good when it freed a font. |
| `ffmpeg` | FFmpeg 9.0.2 without `-ffast-math` (audio files starting at time 0 froze the game for ~20 s), with the MP4 demuxer and H.264 parser for the movie player |
| `cpython` | Grimiku's `cpython-vita` (3.11) at `-O2`. Patches: **timed waits returned at once, which made Python ~3× slower**; faster directory listings. |

The patches are in `patches/`. `--from STEP` redoes a step and everything after it. `--dir` builds somewhere else, and `VITA_TOOLCHAIN` points `vita_port_build.py` at it.

**`vita_port_build.py`** copies `renpy-vita/` and the `renpy-8` and `pygame_sdl2` submodules into `build/toolchain/port-work/<label>/` and runs Cython. It takes the commits your checkout has: RenPy-Vita-8's `HEAD` and the submodule commits it records. `--ref renpy-8=BRANCH` picks another branch or commit, and `--worktree renpy-8` builds from the working tree, including uncommitted changes. It then compiles and links them into `build/port/vita/` (`--label` changes the name), which holds `eboot.bin` and `lib/python311.zip`. Rebuilds are incremental. After changing a library in the toolchain, delete `build/toolchain/port-work/<label>/renpy-vita/build/eboot.elf`; otherwise ninja doesn't notice the change and doesn't relink.

## Running the pipeline

1. **A settings file per game.** Copy `pipeline/games/example.toml` and edit it (settings files in `games/` other than the example aren't committed):

   ```toml
   [game]
   title_id = "ABCD00001"            # the app's ID: 9 capital letters or digits
   # title = "..."                   # default: the game's config.name
   dir = "MyGame-1.0-pc/game"        # the PC release's game/ folder
   renpy = "MyGame-1.0-pc"           # the PC release (its Ren'Py runs the story stage)

   [build]
   # port_build = "build/port/vita"  # the eboot, see "Building the eboot"
   # root = "build/games"            # where outputs go
   ```

2. **Preview** the stage commands without running them:

   ```
   uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml --dry-run
   ```

3. **Run it:**

   ```
   uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml
   ```

   The result is a VPK, `<root>/<TITLE_ID>/full/<TITLE_ID>.vpk`. No Vita is needed. Install it however you like; with VitaShell, the Vita needs free space for the VPK and the installed app.

4. **Or put it straight onto the Vita** (optional), instead of writing a VPK. Later runs only send what changed, which is much faster than reinstalling a VPK. Quit the game on the Vita first.

   - **Over Wi-Fi:** `--deploy` uploads the app folder through [vitacompanion](https://github.com/devnoname120/vitacompanion), which must be running on the Vita. Write the Vita's IP address into `vita_host.txt` in the workspace, or set `VITA_HOST`. The Vita shows the address under Settings > Network > Wi-Fi Settings, in the connection's advanced settings.

     ```
     uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml --deploy
     ```

   - **Over USB:** start VitaShell's USB mode (with the memory card as the USB device), mount the drive that appears on the computer, and pass its mount point with `--usb`. USB is about three times faster than Wi-Fi: about 10–11 MB/s against about 3 MB/s, so a 3 GB game takes about 5 minutes instead of about 15. In the container, start it with that mount point, `run-container.sh --usb /path/to/card`, and use `/media/vita`:

     ```
     uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml --usb /media/vita
     ```

     When it's done, leave the container (or just finish the copy), eject the drive on the computer, and then leave USB mode.

   Both keep the same record of what's on the card, so you can switch between them. After a run that wrote the VPK, `--stages deploy` (with `--usb` for USB) sends the same build without redoing anything.

   A game copied onto the Vita this way also has to be registered with the system before its bubble appears, as installing a VPK would do. A Wi-Fi deploy does that by itself the first time; it takes a few seconds. After a first USB copy, leave USB mode and either:
   - run VitaShell's "Refresh LiveArea", which needs no network, or
   - register it over Wi-Fi: `uv run RenPy-Vita-8/pipeline/vitactl.py register TITLEID`.

5. **Removing a game from the Vita** (optional):

   ```
   uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml --uninstall [--saves]
   ```

   It lists what it will delete and asks first. It removes the app folder and the system's leftovers for its title ID: LiveArea data, licence, system save folder and cache. With `--saves`, it also removes the game's Ren'Py saves in `ux0:/Ren'Py Data/`; deleting a game on the Vita itself leaves those behind. The save folder is found from the game's `config.save_directory`, so it needs an earlier build. It works over Wi-Fi, or with `--usb MOUNT` over USB.

   The home-screen bubble can't be removed remotely: if the game was installed or registered, delete its bubble on the Vita afterwards (hold it, then Delete). `vitactl.py uninstall TITLEID [--saves NAME]` does the same for any title ID.

| Stage | Script | Does |
|---|---|---|
| `extract` | `vita_extract.py` | Takes the files out of the game's archives. Movies go to a separate tree. |
| `video` | `vita_video.py` | Re-encodes movies to 960×540 H.264/AAC MP4 (CRF 18), the formats the Vita decodes. Adds silent sound to movies that have none. |
| `images` | `vita_images.py` | Halves images above 256×256 and crops large transparent margins. A generated hook loads them at the right size and pads them back. |
| `fonts` | `vita_fonts.py` | Cuts fonts above 1 MB down to the characters the scripts use. |
| `presplash` | `vita_presplash_build.py` | Prepares the loading screen. Images much smaller than the screen are enlarged with Real-ESRGAN (`vita_upscale.py`). |
| `sce_sys` | `vita_sce_sys.py` | Writes `param.sfo`, the bubble icon and LiveArea art, and the package header (`package/head.bin`) the system needs to register the app. |
| `story` | `vita_story.py` | Splits the compiled scripts so episodes load when first needed (boot ~76 → 44 s for the test game). |
| `build` | `vita_build.py` | Assembles the app folder: port files, eboot, game, overlays and `rpy/` hooks. Packs assets into archives, because loose files are slow on the Vita. Writes the VPK. |
| `deploy` | `vitactl.py sync` | Only with `--deploy` or `--usb`, or when named in `--stages`: sends only what changed to `ux0:app/TITLE_ID`, over Wi-Fi or USB, and registers a new game (over USB, it says how). |

The app folder ends up in `<root>/<TITLE_ID>/full/app/<TITLE_ID>`, next to the VPK. Reruns only redo what changed: extracting, movies and images skip files that haven't changed since the last run, and the VPK is only rewritten when the app's contents changed. The first full run for the test game takes about 20 minutes, mostly encoding its 449 movies. A rerun with nothing changed takes about 20 s. Its VPK is 3.0 GiB and takes about 10 s to write.

| Option | Does |
|---|---|
| `--variant slice` | Builds only the folders in the settings file's `[slice]` section: a quick test build |
| `--from STAGE` | Starts from a stage |
| `--stages a,b` | Runs only these stages |
| `--deploy` | Uploads the app to the Vita over Wi-Fi instead of writing a VPK |
| `--usb MOUNT` | Copies the app to the Vita's memory card mounted at `MOUNT` (VitaShell's USB mode) instead of writing a VPK |
| `--uninstall` | Removes the game from the Vita (asks first); `--saves` also removes its Ren'Py saves |
| `--vpk` / `--no-vpk` | Writes a VPK, or doesn't. The default is a VPK unless deploying. |
| `--dry-run` | Prints the commands only |

To give a stage extra arguments, add them to the settings file:

```toml
[stages.video]
args = ["--crf", "20.2"]
```

## What's changed in the port

The engine changes are on the `vita-perf` branches of this repository and its two submodules.

- **Movies:** our own player drives the Vita's hardware H.264 decoder, with FFmpeg reading the MP4 and decoding the AAC sound. It plays up to two movies at once, loops without a gap and survives suspend and standby. Frames go straight into GPU memory, so the CPU never copies a picture. Sony's AvPlayer is the fallback.
- **Speed:**
  - image loading: archive reads, decoding, alpha bounds, colour matrices, premultiplication
  - integer division in blurs
  - `os.scandir`
- **Fixes:**
  - quitting crashed the app
  - touch input didn't work with a source-built SDL
  - the Vita didn't dim or go into standby during play
  - consecutive and looping movies
- **gl2 renderer (experimental, off by default):** Ren'Py's shader renderer on the Vita's OpenGL ES 2.0. To switch it on, create an empty file named `vita_gl2` in the app folder. It's as fast as the default renderer in play, but a dissolve between two playing movies drops to ~38 fps.

**The launcher, in `port/`** (shipped to the app folder):
- `renpy.py`: the launcher with Vita settings: phone layout variants, no GPU performance test, renderer choice, a clean exit
- `vita_boot.py`: boot speed-ups (zip imports, file lookups, bytecode cache format, compiled screen expressions, garbage collection)
- `vita_presplash.py`: a GLES loading screen with a progress bar

**Game-side hooks, in `rpy/`** (shipped into `game/`). Each file's header explains its problem and fix.

| Hook | Does |
|---|---|
| `vita_video.rpy` | Plays the `.mp4` when the game asks for its WebM |
| `vita_lazy_images.rpy` | Defines the images under `images/` when first used, not at boot |
| `vita_image_cache.rpy` | Gives the image cache a real size (the port's low-memory mode left it at 0) |
| `vita_font_memory.rpy` | Reads each font into memory once, instead of FreeType reading the card for every glyph |
| `vita_fbo_rtt.rpy` | Renders transitions straight into textures (old renderer) |
| `vita_legacy_transforms.rpy` | Makes perspective, blur and matrixcolor work under the old renderer |
| `vita_alphamask.rpy` | Masks images on the CPU instead of with AlphaMask's shader |
| `vita_movie_channels.rpy` | Creates movie channels only when a movie is used |
| `vita_dynamic_image_cache.rpy` | Stops style-prefixed images being re-resolved at every interaction |
| `vita_predict_menus.rpy` | Preloads the images of menus that on-screen buttons open |
| `vita_stall_clock.rpy` | Stops animations jumping ahead after a slow frame |
| `vita_dpad_focus.rpy` | D-pad navigation for full-screen masked buttons |
| `vita_touch_targets.rpy` | Bigger quick-menu buttons for touch |
| `vita_mobile.rpy` | Lets game code see `renpy.mobile` as True, for touch layouts |
| `vita_gl2.rpy` | Settings for the gl2 renderer |

## The Vita side

- **Remote control:** `vitactl.py` drives the Vita through [vitacompanion](https://github.com/devnoname120/vitacompanion) (commands on port 1338, FTP on 1337):
  - launching and quitting the app
  - button presses
  - file transfer
  - uploading builds
  - boot timing
  - webcam frames through udcd_uvc

  The Vita's address comes from `VITA_HOST` or `vita_host.txt` in the workspace, and the app from `VITA_TITLE_ID` or `vita_title_id.txt`. Launching, quitting and file transfer need Wi-Fi; only copying a build also works over USB.
- **Profiling and crashes:**
  - `vita_framebench.py` boots the game with Ren'Py's frame profiler and reports the slow frames per stage.
  - `vita_crash.py` reads a Vita core dump (`ux0:data/psp2core-*.psp2dmp`) and symbolizes each thread with the build's `eboot.elf`.
- **Before first launch,** the Vita needs what RenPy-Vita-8 itself needs (see the main README).
- **Debugging:** the app writes Ren'Py's `log.txt` in its folder. The launcher writes `vita_fatal.txt` there if Ren'Py fails to start.

## Game-specific notes

The pipeline reads everything from the game's own files and Ren'Py's rules, without hard-coded names. A few things are still worth checking per game:
- Fonts used for text that isn't in any script (typed names, generated text) would lose characters when subset. Keep them with `[stages.fonts] args = ["--keep", "FONT.ttf"]`.
- Some games switch effects or layouts off for mobile variants. The port reports the Vita as `vita small phone touch mobile`.
- `--variant slice` needs a `[slice]` section listing the folders to include.

## Licence

Copyright (C) 2026 LemoneCello and contributors.

The pipeline (everything in `pipeline/`) is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [`LICENSE`](LICENSE) for the full text.

Exceptions:
- **`patches/`:** each patch is under the licence of the project it modifies (SDL, SDL_image, HarfBuzz, FFmpeg, CPython).
- **`port/renpy.py`:** based on Ren'Py's launcher, and keeps Ren'Py's MIT licence (see its header).
- **The `head.bin` template in `vita_sce_sys.py`:** from [VitaShell](https://github.com/TheOfficialFloW/VitaShell) (GPL-3.0, which allows combining it with AGPL code).

The rest of this repository (the port itself) is MIT-licensed, and the games you process keep their own terms.
