#!/usr/bin/env bash
set -euo pipefail

cd /home/startech/yt2mp3
export PYTHONPATH=/home/startech/yt2mp3

exec /home/startech/miniconda3/envs/yt2mp3/bin/python \
  -m uvicorn app.main:app \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-8001}"
