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

`pipeline/container/` has a Debian image with everything the scripts need. `run-container.sh`, started from the workspace, mounts it at `/work` and shares the host network so the container can reach the Vita. The scripts use [uv](https://docs.astral.sh/uv/) (`uv run script.py`); each script declares its own dependencies.

## Running the pipeline

1. **A settings file per game.** Copy `pipeline/games/example.toml` and edit it (settings files in `games/` other than the example aren't committed):

   ```toml
   [game]
   title_id = "ABCD00001"            # the app's ID: 9 capital letters or digits
   # title = "..."                   # default: the game's config.name
   dir = "MyGame-1.0-pc/game"        # the PC release's game/ folder
   renpy = "MyGame-1.0-pc"           # the PC release (its Ren'Py runs the story stage)

   [build]
   port_build = "build/port/zc"      # the eboot, see "Building the eboot"
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

4. **Or upload straight to the Vita** (optional): `--deploy` uploads the app folder over Wi-Fi through vitacompanion (see "The Vita side") instead of writing a VPK. Later runs only upload what changed, which is much faster than reinstalling a VPK. Quit the game on the Vita first.

   ```
   uv run RenPy-Vita-8/pipeline/vita_pipeline.py mygame.toml --deploy
   ```

| Stage | Script | Does |
|---|---|---|
| `extract` | `vita_extract.py` | Takes the files out of the game's archives. Movies go to a separate tree. |
| `video` | `vita_video.py` | Re-encodes movies to 960×540 H.264/AAC MP4 (CRF 18), the formats the Vita decodes. Adds silent sound to movies that have none. |
| `images` | `vita_images.py` | Halves images above 256×256 and crops large transparent margins. A generated hook loads them at the right size and pads them back. |
| `fonts` | `vita_fonts.py` | Cuts fonts above 1 MB down to the characters the scripts use. |
| `presplash` | `vita_presplash_build.py` | Prepares the loading screen. Images much smaller than the screen are enlarged with Real-ESRGAN (`vita_upscale.py`). |
| `sce_sys` | `vita_sce_sys.py` | Writes `param.sfo` and the bubble icon and LiveArea art. |
| `story` | `vita_story.py` | Splits the compiled scripts so episodes load when first needed (boot ~76 → 44 s for the test game). |
| `build` | `vita_build.py` | Assembles the app folder: port files, eboot, game, overlays and `rpy/` hooks. Packs assets into archives, because loose files are slow on the Vita. Writes the VPK. |
| `deploy` | `vitactl.py sync` | Only with `--deploy`: uploads only what changed, to `ux0:app/TITLE_ID`. |

The app folder ends up in `<root>/<TITLE_ID>/full/app/<TITLE_ID>`, next to the VPK. Every stage is incremental. The first full run for the test game takes about 20 minutes, mostly encoding its 449 movies; its VPK is 3.0 GiB and takes under a minute to write.

| Option | Does |
|---|---|
| `--variant slice` | Builds only the folders in the settings file's `[slice]` section: a quick test build |
| `--from STAGE` | Starts from a stage |
| `--stages a,b` | Runs only these stages |
| `--deploy` | Uploads the app to the Vita instead of writing a VPK |
| `--vpk` / `--no-vpk` | Writes a VPK, or doesn't. The default is a VPK unless deploying. |
| `--dry-run` | Prints the commands only |

To give a stage extra arguments, add them to the settings file:

```toml
[stages.video]
args = ["--crf", "20.2"]
```

## Building the eboot

The eboot is built once and is the same for every game:

```
uv run RenPy-Vita-8/pipeline/vita_port_build.py --label zc --worktree renpy-vita \
    --ref renpy-8=vita-perf --ref pygame_sdl2=vita-perf
```

It copies `renpy-vita/` and the `renpy-8` and `pygame_sdl2` submodules (branch `vita-perf`) into `build/toolchain/port-work/<label>/` and runs Cython. It then compiles and links them into `build/port/<label>/`, which holds `eboot.bin` and `lib/python311.zip`. Rebuilds are incremental.

It needs a toolchain in `build/toolchain/`:

- **VitaSDK**: `vdpm/bootstrap-vitasdk.sh --install-dir build/toolchain/vitasdk`, then install these libraries with `vdpm`: taihen, sdl2_mixer, sdl2_image, sdl2_ttf, sdl2_gfx, ffmpeg, libwebp, libvorbis, libogg, mpg123, flac, libmikmod, opus, lame, freetype, harfbuzz, libjpeg-turbo, libpng, bzip2, openssl-1.1.1, zlib, libintl, fribidi, libzip, xz.
- **PVR_PSP2 v3.9**: its `include/` folders (EGL, GLES, GLES2, KHR, gpu_es4) go into the SDK's include directory, and its stub libraries into the SDK's lib directory.
- **A host Python 3.11** in `build/toolchain/hostpy/` with Cython 0.29.37 (not 3.x), setuptools and `future`.
- **Rebuilt libraries**, replacing the vdpm versions:

  | Library | How | Why |
  |---|---|---|
  | SDL2 2.32.8 | `-DVIDEO_VITA_PVR=ON`, plus `patches/sdl2-2.32.8-vita-pvr-ime.patch` and `sdl2-2.32.8-vita-stdio-buffer.patch` | PVR video driver; the on-screen keyboard crashed; file reads were 3–4× slow |
  | SDL2_image 2.8.12 | `-DSDL2IMAGE_BACKEND_STB=OFF`, plus `patches/sdl2_image-2.8.12-jpeg-rgba.patch` then `sdl2_image-2.8.12-vita-hw-jpeg.patch` | Real libjpeg/libpng, JPEG straight to RGBA, the hardware JPEG decoder for background loads |
  | HarfBuzz 14.5.0 | `patches/harfbuzz-hb-ft-static-face-deadlock.patch` | The game hung for good when it freed a font |
  | libjpeg-turbo 3.2.0 | `-DWITH_SIMD=TRUE` (NEON) | JPEG decoding ~40% faster |
  | FFmpeg 9.0.2 | `patches/ffmpeg-9.0.2-vita-build.sh`: without `-ffast-math`, with the MP4 demuxer and H.264 parser | Audio files starting at time 0 froze the game for ~20 s; MP4 support for the movie player |
  | zlib-ng 2.2.4 | `patches/zlib-ng-2.2.4-vita-build.sh` (compat mode, NEON) | Faster PNG decoding |
  | CPython 3.11 | Grimiku's `cpython-vita` (branch `patch-1`) at `-O2`, plus `patches/cpython-vita-timed-waits.patch` and `cpython-vita-scandir-d-type.patch` | **Essential**: timed waits returned at once, which made Python ~3× slower; directory listings faster |

After replacing an SDK library, delete `build/toolchain/port-work/<label>/renpy-vita/build/eboot.elf`. Otherwise ninja doesn't notice the change and doesn't relink.

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

  `VITA_HOST` sets the Vita's address and `VITA_TITLE_ID` the app.
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
