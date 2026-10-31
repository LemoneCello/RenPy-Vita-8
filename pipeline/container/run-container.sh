#!/usr/bin/env bash
# Start the Vita port workspace container. Run it from the workspace folder
# (the one holding RenPy-Vita-8/, the games and build/):
#
#   RenPy-Vita-8/pipeline/container/run-container.sh [--build] [--dev] [--usb MOUNT] [COMMAND...]
#
#   --build       rebuild the image first
#   --usb MOUNT   share the Vita's memory card, mounted on this computer at
#                 MOUNT (VitaShell's USB mode or a card reader), as /media/vita, for
#                 `vita_pipeline.py --usb /media/vita`; mount it first
#   --dev         for working on the pipeline itself: the dev image (sudo,
#                 Claude Code, desktop test runs), the host's network, and the
#                 Vita's webcam feed (udcd_uvc) if it's plugged in
#
# The container sees only the workspace (mounted at /work; VITA_WORKSPACE
# overrides the current directory), plus the memory card with --usb. It
# reaches the Vita over Wi-Fi through Docker's usual network. Home (uv cache,
# and in dev mode the Claude login) lives in a named volume. Settings can go
# in vita-container.env in the workspace: VITA_DEV=1 (always --dev),
# VITA_IMAGE, VITA_CONTAINER, VITA_HOME_VOLUME, VITA_VIDEO (webcam device),
# VITA_HOTSPOT (a NetworkManager connection the Vita uses, checked in dev mode).
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROJECT="$(cd "${VITA_WORKSPACE:-$PWD}" && pwd)"

if [[ -f "$PROJECT/vita-container.env" ]]; then
    source "$PROJECT/vita-container.env"
fi

build=0
dev="${VITA_DEV:-0}"
usb=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --build) build=1; shift ;;
        --dev) dev=1; shift ;;
        --usb) usb="${2:?--usb needs the mount point of the memory card}"; shift 2 ;;
        *) break ;;
    esac
done

if [[ "$dev" == 1 ]]; then
    TARGET=dev
    IMAGE="${VITA_IMAGE:-renpy-vita-workspace-dev}"
else
    TARGET=base
    IMAGE="${VITA_IMAGE:-renpy-vita-workspace}"
fi
NAME="${VITA_CONTAINER:-renpy-vita}"
HOME_VOLUME="${VITA_HOME_VOLUME:-renpy-vita-home}"

if [[ "$build" == 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build --target "$TARGET" -t "$IMAGE" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$HERE"
fi

run_args=()

if [[ -n "$usb" ]]; then
    if [[ ! -d "$usb/app" ]]; then
        echo "$usb doesn't look like the Vita's memory card (no app/ folder); mount it first" >&2
        exit 1
    fi
    run_args+=(-v "$(cd "$usb" && pwd):/media/vita")
fi

if [[ "$dev" == 1 ]]; then
    run_args+=(--network host)
    if [[ -n "${VITA_HOTSPOT:-}" ]] && ! nmcli -t -f NAME con show --active 2>/dev/null | grep -qx "$VITA_HOTSPOT"; then
        echo "note: $VITA_HOTSPOT is not active (start it with: nmcli con up $VITA_HOTSPOT)" >&2
    fi
    VIDEO="${VITA_VIDEO:-/dev/video0}"
    if [[ -e "$VIDEO" ]]; then
        run_args+=(--device "$VIDEO" --group-add "$(stat -c %g "$VIDEO")")
    else
        echo "note: $VIDEO not present; Vita screen feed unavailable (plug in the USB cable)" >&2
    fi
fi

exec docker run -it --rm \
    --name "$NAME" \
    --hostname "$NAME" \
    "${run_args[@]}" \
    -v "$PROJECT:/work" \
    -v "$HOME_VOLUME:/home/dev" \
    -e TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" \
    "$IMAGE" "$@"
