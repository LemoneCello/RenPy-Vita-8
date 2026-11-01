# Workspace for porting Ren'Py games to the Vita. Built from container/ only,
# so the game folders never enter the build context. Two targets:
#
#   base  what the pipeline needs: the eboot's toolchain build, FFmpeg for the
#         movies, uv for the stage scripts. No sudo. (The default.)
#   dev   for working on the pipeline itself: adds sudo, Claude Code with rtk
#         (condensed command output), the Vita webcam feed (udcd_uvc) tools
#         and Xvfb for desktop Ren'Py test runs. run-container.sh --dev uses it.

FROM debian:bookworm-slim AS base

ARG UID=1000
ARG GID=1000

# Building the eboot (VitaSDK via vdpm, the rebuilt libraries, CPython for the
# Vita, Ren'Py's Cython modules), re-encoding movies, and the graphics
# libraries the PC release's own Ren'Py may load when vita_story.py runs it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git less procps \
        python3 ffmpeg \
        build-essential cmake ninja-build pkg-config \
        autoconf automake libtool texinfo patch file \
        bzip2 xz-utils unzip zip wget p7zip-full rsync \
        libgl1 libgl1-mesa-dri libegl1 libx11-6 libxext6 libxi6 libxrandr2 \
        libxcursor1 libxinerama1 libxss1 libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*

# A fixed uv, so `uv run` behaves the same everywhere.
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /usr/local/bin/

# The workspace's files belong to the user who runs the container.
RUN groupadd -g ${GID} dev && useradd -m -u ${UID} -g ${GID} -s /bin/bash dev

USER dev
WORKDIR /work
ENV UV_CACHE_DIR=/home/dev/.cache/uv

CMD ["bash"]


FROM base AS dev

USER root

# Vita webcam feed and port checks, desktop test runs, Claude Code; sudo
# lets the workspace add anything else without an image rebuild.
RUN apt-get update && apt-get install -y --no-install-recommends \
        v4l-utils socat netcat-openbsd \
        xvfb xauth \
        nodejs npm \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev && chmod 0440 /etc/sudoers.d/dev

# Installed system-wide so the persistent home volume never shadows it.
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# rtk (condensed command output for Claude Code), system-wide like Claude Code
# so the home volume never shadows it.
ARG RTK_VERSION=v1.1.0
RUN curl -fsSL "https://raw.githubusercontent.com/rtk-ai/rtk/refs/tags/${RTK_VERSION}/install.sh" \
        | RTK_VERSION=${RTK_VERSION} RTK_INSTALL_DIR=/usr/local/bin sh

USER dev

# rtk's Claude Code hook and instructions, in ~/.claude: Docker copies the
# image's /home/dev into a new, empty home volume, so this sets up a fresh
# one; an existing volume keeps its own settings (run `rtk init -g` there).
RUN rtk init -g --auto-patch

# Desktop Ren'Py test runs: no sound card, software GL on a virtual display.
ENV SDL_AUDIODRIVER=dummy \
    LIBGL_ALWAYS_SOFTWARE=1
