#!/usr/bin/env bash
# Build the Strixper dashboard Docker image.
#
# Usage:
#   ./build_img.sh                  # -> strixper-dashboard:latest
#   ./build_img.sh strixper:v2      # -> custom tag
#   NO_CACHE=1 ./build_img.sh       # rebuild without the layer cache
#
# The image is multi-stage (React build -> Python runtime with the docker
# CLI). See the README "Docker quick start" for the run command -- the
# container needs the host docker socket mounted and host networking.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${1:-strixper-dashboard:latest}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[build_img] ERROR: docker not found on PATH" >&2
  exit 1
fi

BUILD_ARGS=()
if [ "${NO_CACHE:-0}" = "1" ]; then
  BUILD_ARGS+=(--no-cache)
fi

echo "[build_img] building ${IMAGE} from ${ROOT}"
docker build "${BUILD_ARGS[@]}" -t "${IMAGE}" "${ROOT}"
echo "[build_img] built ${IMAGE}"
