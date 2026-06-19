# yt2mp3

Private, mobile-friendly web app that analyzes the likely global key of an
authorized YouTube video, shifts it by -3 to +3 semitones without changing its
speed, and produces an MP3.

Use this application only with content you own or are authorized to process.
It is not a general-purpose downloader and does not bypass DRM, private videos,
regional restrictions, or YouTube authentication controls.

## Requirements

- Python 3.12
- FFmpeg and ffprobe
- Rubber Band CLI 3.x or 4.x

Supported development/deployment targets are macOS Apple Silicon and Linux
x86_64 (WSL, Docker, and Render). Native Windows and Intel macOS are not tested.

On macOS with Homebrew:

```bash
brew install python@3.12 ffmpeg rubberband
```

## Local setup

```bash
python3.12 -m venv --prompt yt2mp3 .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. Development mode does not require a password
unless `APP_PASSWORD` is set.

Run tests:

```bash
pytest -q
```

## Configuration

Copy `.env.example` to `.env`. Production refuses to start without
`APP_PASSWORD` and a non-default `SESSION_SECRET` of at least 32 characters.
The service uses one signed session cookie; it has no accounts or database.

Important defaults:

- maximum video duration: 15 minutes
- maximum source size: 150 MB
- queue size and concurrency: 5 and 1
- job TTL: 60 minutes
- work directory: `/tmp/yks`

All files are temporary. Polling does not extend TTL, and a process or Render
restart invalidates every in-memory job.

## Docker

Docker images always use a Linux platform. On an Apple Silicon Mac, the native
platform is `linux/arm64` (not `darwin/arm64`). Build it explicitly when the
image will run locally:

```bash
docker build --platform linux/arm64 -t yt2mp3 .
docker run --rm -p 8000:8000 \
  -e APP_ENV=production \
  -e APP_PASSWORD='replace-me' \
  -e SESSION_SECRET='replace-with-at-least-32-random-characters' \
  yt2mp3
```

Use `--platform linux/amd64` only when the target server requires AMD64. An
explicit `--platform`, the `DOCKER_DEFAULT_PLATFORM` environment variable, or a
remote builder can override the Mac's native architecture. Check an image with:

```bash
docker image inspect yt2mp3:latest \
  --format '{{.Os}}/{{.Architecture}}'
```

Check `http://127.0.0.1:8000/health`. The health endpoint only checks whether
local executables are available; it never contacts YouTube.

## Render

1. Push this repository to a Git provider.
2. Create a Render Blueprint from `render.yaml`.
3. Set `APP_PASSWORD`; Render generates `SESSION_SECRET`.
4. Deploy and verify `/health`, then log in through the generated HTTPS URL.

The Blueprint defaults to a paid Starter service because audio analysis and
pitch shifting are CPU- and memory-intensive. Confirm current Render pricing
and resource limits before deployment.

## Architecture and limitations

FastAPI serves the static frontend and owns an in-memory queue. A single worker
runs yt-dlp, ffmpeg/ffprobe, librosa, and Rubber Band using argument arrays and
separate process groups. Subprocesses have timeouts and are terminated when a
running job is deleted.

- Key detection is a reproducible Krumhansl–Schmuckler profile comparison. Its
  confidence value is a relative UI indicator, not a statistical probability.
- Relative major/minor keys and songs that change key can be misidentified.
- YouTube may reject datacenter IP addresses with 403, 429, or bot verification.
- Only standard watch, youtu.be, mobile watch, and Shorts URLs are accepted.
- Playlists, channels, livestreams, and arbitrary sites are rejected.
- The latest two selected shift outputs are retained until job expiry.

## Dependency updates

The four requirements files separate direct dependencies from reproducible
locks and production dependencies from development tools:

- `requirements.in`: direct runtime dependencies, edited by hand
- `requirements.txt`: pinned runtime dependency tree, used by Docker
- `requirements-dev.in`: direct development dependencies plus runtime ones
- `requirements-dev.txt`: pinned development and runtime environment, used by
  local setup and tests

Edit `requirements.in` or `requirements-dev.in`, then regenerate locks:

```bash
pip-compile --output-file requirements.txt requirements.in
pip-compile --output-file requirements-dev.txt requirements-dev.in
```

Review `THIRD_PARTY_NOTICES.md` whenever dependencies or system packages change.
