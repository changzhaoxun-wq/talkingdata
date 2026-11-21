#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
name="${CONTAINER_NAME:-talkingdata-workbench}"
image="${IMAGE_NAME:-talkingdata-workbench:local}"
docker build --target runtime -t "$image" .
if docker container inspect "$name" >/dev/null 2>&1; then
  docker stop --time 15 "$name"
  docker rm "$name"
fi
bash scripts/start.sh
