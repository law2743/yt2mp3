#!/usr/bin/env bash
set -euo pipefail

api_url="${BACKEND_API_URL:-}"
if [[ ! "$api_url" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$ ]]; then
  echo "BACKEND_API_URL must be an HTTP(S) origin without a path" >&2
  exit 1
fi

api_url="${api_url%/}"
printf 'window.YT2MP3_CONFIG = Object.freeze({\n  apiBaseUrl: "%s",\n});\n' "$api_url" \
  > frontend/config.js
