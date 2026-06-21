from __future__ import annotations

import re
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.errors import AppError
from app.models import SourceInfo
from app.services.files import safe_child
from app.services.process import ProcessFailed, ProcessTimedOut, run_process

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
PROGRESS_RE = re.compile(r"yt2mp3-progress:\s*([0-9]+(?:\.[0-9]+)?)%")


@dataclass(frozen=True, slots=True)
class CanonicalYouTubeUrl:
    video_id: str
    url: str


def canonicalize_youtube_url(raw_url: str) -> CanonicalYouTubeUrl:
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError as exc:
        raise _invalid_url() from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise _invalid_url()

    if "list" in parse_qs(parsed.query):
        raise AppError(400, "PLAYLIST_NOT_SUPPORTED", "目前不支援播放清單。")

    video_id: str | None = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be":
        if len(path_parts) == 1:
            video_id = path_parts[0]
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif len(path_parts) == 2 and path_parts[0] == "shorts":
        video_id = path_parts[1]

    if not video_id or not VIDEO_ID_RE.fullmatch(video_id):
        raise _invalid_url()
    return CanonicalYouTubeUrl(video_id, f"https://www.youtube.com/watch?v={video_id}")


def _invalid_url() -> AppError:
    return AppError(400, "INVALID_YOUTUBE_URL", "請輸入有效的 YouTube 單一影片網址。")


class YouTubeAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _base_args(self) -> list[str]:
        args = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--socket-timeout",
            "15",
            "--retries",
            "2",
        ]
        if self.settings.ytdlp_cookies_file:
            args.extend(["--cookies", str(self.settings.ytdlp_cookies_file)])
        return args

    async def metadata(self, url: CanonicalYouTubeUrl) -> tuple[SourceInfo, dict]:
        try:
            result = await run_process(
                self._base_args()
                + ["-f", "bestaudio/best", "--dump-single-json", "--skip-download", url.url],
                timeout=self.settings.metadata_timeout_seconds,
            )
            raw = json.loads(result.stdout)
        except ProcessTimedOut as exc:
            raise AppError(504, "PROCESS_TIMEOUT", "讀取影片資訊超過時間限制。", True) from exc
        except ProcessFailed as exc:
            raise map_ytdlp_error(exc.stderr) from exc
        except json.JSONDecodeError as exc:
            raise AppError(502, "AUDIO_FORMAT_UNAVAILABLE", "無法讀取影片資訊。", True) from exc

        if raw.get("is_live") or raw.get("live_status") in {"is_live", "is_upcoming"}:
            raise AppError(400, "LIVE_NOT_SUPPORTED", "目前不支援直播影片。")
        duration = int(raw.get("duration") or 0)
        if duration <= 0:
            raise AppError(502, "AUDIO_FORMAT_UNAVAILABLE", "無法確認影片長度。")
        if duration > self.settings.max_video_duration_seconds:
            raise AppError(400, "VIDEO_TOO_LONG", "影片超過可處理的長度限制。")
        source_size = selected_source_size(raw)
        if source_size and source_size > self.settings.max_source_mb * 1024 * 1024:
            raise AppError(413, "SOURCE_TOO_LARGE", "音訊檔案超過處理上限。")
        source = SourceInfo(
            video_id=url.video_id,
            title=str(raw.get("title") or "Untitled")[:500],
            uploader=(str(raw.get("uploader"))[:300] if raw.get("uploader") else None),
            duration_seconds=duration,
        )
        return source, raw

    async def download(
        self,
        url: CanonicalYouTubeUrl,
        job_root: Path,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Path:
        output_template = str(safe_child(job_root, "source.%(ext)s"))
        args = self._base_args() + [
            "--newline",
            "--no-color",
            "--progress-template",
            "download:yt2mp3-progress:%(progress._percent_str)s",
            "-f",
            "bestaudio/best",
            "--max-filesize",
            f"{self.settings.max_source_mb}M",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "-o",
            output_template,
            url.url,
        ]

        def report_progress(line: str) -> None:
            match = PROGRESS_RE.search(line)
            if match and progress_callback:
                progress_callback(min(100, max(0, round(float(match.group(1))))))

        try:
            await run_process(
                args,
                timeout=self.settings.download_timeout_seconds,
                stdout_line_callback=report_progress,
                stderr_line_callback=report_progress,
            )
        except ProcessTimedOut as exc:
            raise AppError(504, "PROCESS_TIMEOUT", "下載音訊超過時間限制。", True) from exc
        except ProcessFailed as exc:
            raise map_ytdlp_error(exc.stderr) from exc

        sources = [
            path
            for path in job_root.glob("source.*")
            if path.suffix not in {".jpg", ".part", ".ytdl"}
        ]
        if not sources:
            raise AppError(502, "AUDIO_FORMAT_UNAVAILABLE", "找不到可處理的音訊格式。")
        source = max(sources, key=lambda item: item.stat().st_size)
        if source.stat().st_size > self.settings.max_source_mb * 1024 * 1024:
            source.unlink(missing_ok=True)
            raise AppError(413, "SOURCE_TOO_LARGE", "音訊檔案超過處理上限。")
        thumbnails = list(job_root.glob("source*.jpg"))
        if thumbnails:
            thumbnails[0].replace(safe_child(job_root, "thumbnail.jpg"))
        return source


def map_ytdlp_error(stderr: str) -> AppError:
    text = stderr.lower()
    if (
        "sign in" in text
        or "confirm you’re not a bot" in text
        or "confirm you're not a bot" in text
    ):
        return AppError(
            502, "YOUTUBE_AUTH_REQUIRED", "YouTube 要求額外驗證，目前無法取得此影片。", True
        )
    if "429" in text or "too many requests" in text:
        return AppError(429, "YOUTUBE_RATE_LIMITED", "YouTube 暫時限制此服務，請稍後再試。", True)
    if "private video" in text or "not available in your country" in text:
        return AppError(502, "VIDEO_UNAVAILABLE", "此影片為私人影片或受地區限制。")
    if "requested format is not available" in text or "no video formats" in text:
        return AppError(502, "AUDIO_FORMAT_UNAVAILABLE", "找不到可處理的音訊格式。")
    if "larger than max-filesize" in text:
        return AppError(413, "SOURCE_TOO_LARGE", "音訊檔案超過處理上限。")
    return AppError(502, "YOUTUBE_DOWNLOAD_FAILED", "目前無法取得 YouTube 音訊。", True)


def selected_source_size(metadata: dict) -> int | None:
    """Return yt-dlp's selected-format size, preferring exact over estimated values."""
    candidates = [metadata]
    requested = metadata.get("requested_downloads")
    if isinstance(requested, list):
        candidates = [item for item in requested if isinstance(item, dict)] + candidates
    for key in ("filesize", "filesize_approx"):
        for item in candidates:
            value = item.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
    return None
