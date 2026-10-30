# Workspace for porting Ren'Py games to the Vita: stage scripts, Vita remote control
# (vitacompanion over the host's hotspot, udcd_uvc webcam feed) and headless
# desktop Ren'Py test runs. Built from container/ only, so the game
# folders never enter the build context.

FROM debian:bookworm-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git less procps \
        python3 \
        ffmpeg v4l-utils socat netcat-openbsd \
        xvfb xauth \
        libgl1 libgl1-mesa-dri libegl1 libx11-6 libxext6 libxi6 libxrandr2 \
        libxcursor1 libxinerama1 libxss1 libxxf86vm1 \
        nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Building the port's eboot (VitaSDK via vdpm, then cpython-vita, SDL2, the
# libraries in renpy-vita/CMakeLists.txt, and Ren'Py's Cython modules with a
# host Python 3.11 + Cython 0.29.37 from uv). sudo lets the workspace add
# anything else the build turns out to need without an image rebuild.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build pkg-config \
        autoconf automake libtool texinfo patch file \
        bzip2 xz-utils unzip zip wget p7zip-full rsync \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Same uv as the host, so `uv run` behaves identically.
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /usr/local/bin/

# Installed system-wide so the persistent home volume never shadows it.
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

RUN groupadd -g ${GID} dev && useradd -m -u ${UID} -g ${GID} -s /bin/bash dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev && chmod 0440 /etc/sudoers.d/dev

USER dev
WORKDIR /work

# Desktop Ren'Py test runs: no sound card, software GL on a virtual display.
ENV SDL_AUDIODRIVER=dummy \
    LIBGL_ALWAYS_SOFTWARE=1 \
    UV_CACHE_DIR=/home/dev/.cache/uv

CMD ["bash"]
