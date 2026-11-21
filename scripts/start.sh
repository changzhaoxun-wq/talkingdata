#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi
workbench_port="${PORT:-$(sed -n 's/^PORT=//p' .env | tail -n 1 | tr -d '\r')}"
workbench_port="${workbench_port:-8080}"
if ! [[ "$workbench_port" =~ ^[0-9]+$ ]] || ((workbench_port < 1024 || workbench_port > 65535)); then
  printf 'PORT must be between 1024 and 65535.\n'
  exit 1
fi
name="${CONTAINER_NAME:-talkingdata-workbench}"
image="${IMAGE_NAME:-talkingdata-workbench:local}"
volume="${DATA_VOLUME:-talkingdata-workbench-data}"
docker build --target runtime -t "$image" .
if docker container inspect "$name" >/dev/null 2>&1; then
  printf 'Container %s already exists. Use scripts/restart.sh to replace it.\n' "$name"
  exit 1
fi
docker run -d --name "$name" --restart unless-stopped \
  --env-file .env --env DATA_DIR=/data \
  --publish "127.0.0.1:${workbench_port}:8080" \
  --mount "type=volume,source=${volume},target=/data" \
  --memory 2g --cpus 2 --pids-limit 256 --cap-drop ALL \
  --security-opt no-new-privileges:true "$image"
printf 'Orbit is starting: http://localhost:%s\n' "$workbench_port"
