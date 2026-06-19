#!/usr/bin/env sh
set -eu

base_url="${BASE_URL:-http://127.0.0.1:8000}"
curl --fail --silent --show-error "${base_url}/health"
printf '\n'

