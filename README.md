# yt2mp3

Private, mobile-friendly tool that analyzes the likely global key of an authorized
YouTube video, shifts it without changing speed, and produces an MP3.

The static frontend runs on Render. FastAPI, yt-dlp, FFmpeg, librosa, and Rubber
Band stay on your own computer and are exposed only to your Tailscale tailnet.

```text
Render Static Site -> browser -> Tailscale Serve -> local FastAPI
```

Use this application only with content you own or are authorized to process. It
does not bypass DRM, private videos, regional restrictions, or YouTube controls.

## Requirements

- Python 3.12
- FFmpeg and ffprobe
- Rubber Band CLI 3.x or 4.x
- Tailscale on the backend computer and each client device

## Development

The repository's tested Conda setup is:

```bash
conda create -n yt2mp3 --override-channels -c conda-forge python=3.12 pip ffmpeg
conda activate yt2mp3
sudo apt install rubberband-cli
python -m pip install -r requirements-dev.txt
pytest -q
```

Start the API:

```bash
uvicorn app.main:app --reload
```

Serve the frontend from a second terminal (do not open the HTML with `file://`):

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500`. Development mode allows API calls without a
password. To exercise production authentication locally, copy `.env.example` to
`.env`, set its values, and restart the API.

## Local backend

Generate a token secret and configure the exact Render origin:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `APP_PASSWORD`, `TOKEN_SECRET`, and `CORS_ALLOWED_ORIGINS` in `.env`. Origins
are comma-separated and must not contain paths or `*`.

Run the backend persistently with Docker Compose:

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The container restarts automatically and publishes port 8000 only on loopback.
Jobs and audio are temporary and disappear when their TTL expires or when the
container is replaced.

Expose it privately through Tailscale:

```bash
tailscale serve --bg 8000
tailscale serve status
```

Use the HTTPS URL shown by `tailscale serve status` as `BACKEND_API_URL`. Client
devices must be signed into the same tailnet. The computer must remain awake.

`tailscale funnel` is intentionally not the default. Funnel makes the API public
and should only be enabled after reviewing authentication, rate limits, bandwidth,
and abuse controls.

## Render static frontend

`render.yaml` creates a new static site named `yt2mp3-frontend`. It does not alter
the old Docker Web Service because Render service runtimes are immutable.

1. Push this branch to your Git provider.
2. Create a new Render Blueprint from `render.yaml`.
3. Set `BACKEND_API_URL` to the Tailscale Serve HTTPS URL, without a trailing slash.
4. Set the resulting Render origin in the backend's `CORS_ALLOWED_ORIGINS` and
   restart the backend.
5. Open the Render URL from a device connected to the same tailnet.

The build script writes the public backend URL to `frontend/config.js`. It never
places `APP_PASSWORD`, `TOKEN_SECRET`, or other secrets in the static frontend.

## Authentication and downloads

`POST /api/auth/login` exchanges the shared password for a signed, short-lived
Bearer token. The browser stores it in `sessionStorage`; closing the tab removes
it. Each token owns its own jobs. Thumbnails and MP3 files are fetched with the
`Authorization` header and converted to browser object URLs, so there is no public
download route.

Failed logins are limited per backend-visible client address. When using a reverse
proxy, do not trust arbitrary forwarded-IP headers unless the proxy boundary is
also locked down.

## Configuration

Important variables are documented in `.env.example`:

- `APP_PASSWORD`: shared login password; required in production
- `TOKEN_SECRET`: at least 32 non-default characters; required in production
- `CORS_ALLOWED_ORIGINS`: exact comma-separated static frontend origins
- `ACCESS_TOKEN_TTL_MINUTES`: token lifetime, default 60 minutes
- `JOB_TTL_MINUTES`: job and temporary file lifetime, default 60 minutes
- `MAX_QUEUE_SIZE` / `MAX_CONCURRENT_JOBS`: local resource limits
- `MAX_VIDEO_DURATION_SECONDS` / `MAX_SOURCE_MB`: input limits
- `WORK_ROOT`: local temporary work directory

The queue and job registry live in memory. A backend restart invalidates jobs but
does not invalidate already issued tokens; their signatures remain valid until
expiry as long as `TOKEN_SECRET` is unchanged.

## Architecture and limitations

FastAPI owns an in-memory queue. A single worker runs yt-dlp, ffmpeg/ffprobe,
librosa, and Rubber Band using argument arrays and separate process groups.
Subprocesses have timeouts and are terminated when a running job is deleted.

- Key detection is a reproducible Krumhansl-Schmuckler profile comparison.
- Relative major/minor keys and songs that change key can be misidentified.
- YouTube can reject requests with 403, 429, or bot verification.
- Only individual watch, youtu.be, mobile watch, and Shorts URLs are accepted.
- Playlists, livestreams, channels, and arbitrary sites are rejected.
- MP3 traffic uses the backend computer's upload bandwidth.

## Dependency updates

Edit `requirements.in` or `requirements-dev.in`, then regenerate locks:

```bash
pip-compile --output-file requirements.txt requirements.in
pip-compile --output-file requirements-dev.txt requirements-dev.in
```

Review `THIRD_PARTY_NOTICES.md` whenever dependencies or system packages change.
