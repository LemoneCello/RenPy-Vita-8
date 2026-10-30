#!/usr/bin/env bash
# Start the Vita port workspace container. Run it from the workspace folder
# (the one holding RenPy-Vita-8/, the games and build/):
#
#   RenPy-Vita-8/pipeline/container/run-container.sh            interactive shell
#   RenPy-Vita-8/pipeline/container/run-container.sh claude     Claude Code inside the container
#   RenPy-Vita-8/pipeline/container/run-container.sh --build    rebuild the image first
#
# The container sees only the workspace (mounted at /work; VITA_WORKSPACE
# overrides the current directory). It shares the
# host network so it can reach the Vita on the vita-ap hotspot, and gets the
# udcd_uvc webcam if the Vita is plugged in. Home (Claude login, uv cache) lives
# in a named volume. Names can be changed in vita-container.env in the
# workspace (VITA_IMAGE, VITA_CONTAINER, VITA_HOME_VOLUME).
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PROJECT="$(cd "${VITA_WORKSPACE:-$PWD}" && pwd)"

if [[ -f "$PROJECT/vita-container.env" ]]; then
    source "$PROJECT/vita-container.env"
fi

IMAGE="${VITA_IMAGE:-renpy-vita-workspace}"
NAME="${VITA_CONTAINER:-renpy-vita}"
HOME_VOLUME="${VITA_HOME_VOLUME:-renpy-vita-home}"
VIDEO="${VITA_VIDEO:-/dev/video0}"

if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    [[ "${1:-}" == "--build" ]] && shift
    docker build -t "$IMAGE" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$HERE"
fi

if ! nmcli -t -f NAME con show --active 2>/dev/null | grep -qx vita-ap; then
    echo "note: vita-ap hotspot is not active (start it with: nmcli con up vita-ap)" >&2
fi

device_args=()
if [[ -e "$VIDEO" ]]; then
    device_args=(--device "$VIDEO" --group-add "$(stat -c %g "$VIDEO")")
else
    echo "note: $VIDEO not present; Vita screen feed unavailable (plug in the USB cable)" >&2
fi

exec docker run -it --rm \
    --name "$NAME" \
    --hostname "$NAME" \
    --network host \
    "${device_args[@]}" \
    -v "$PROJECT:/work" \
    -v "$HOME_VOLUME:/home/dev" \
    -e TZ="$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" \
    "$IMAGE" "$@"
