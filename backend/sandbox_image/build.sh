#!/usr/bin/env bash
# Build the CUA per-run sandbox image.
set -euo pipefail
cd "$(dirname "$0")"
IMAGE="${CUA_SANDBOX_IMAGE:-cua-sandbox:latest}"
echo "building $IMAGE ..."
docker build -t "$IMAGE" .
echo "done: $IMAGE"
