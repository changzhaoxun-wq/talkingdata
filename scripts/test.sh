#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --target test -t talkingdata-workbench:test .
docker run --rm --network none talkingdata-workbench:test
