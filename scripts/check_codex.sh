#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONNOUSERSITE=1
export NUMBA_CACHE_DIR="${NUMBA_CACHE_DIR:-/tmp/yt2mp3-numba-cache-$USER}"
mkdir -p "$NUMBA_CACHE_DIR"

PYTHON_BIN="${PYTHON_BIN:-/home/startech/miniconda3/envs/yt2mp3/bin/python}"

echo "pwd=$(pwd)"
echo "python=$PYTHON_BIN"
"$PYTHON_BIN" -V
echo "NUMBA_CACHE_DIR=$NUMBA_CACHE_DIR"

ruff check app tests scripts

timeout 180s "$PYTHON_BIN" -m pytest -q
echo "pytest_exit_code=$?"
