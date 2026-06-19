import pytest

from app.config import Settings
from app.errors import AppError
from app.services.process import ProcessResult
from app.services.youtube import (
    YouTubeAdapter,
    canonicalize_youtube_url,
    map_ytdlp_error,
    selected_source_size,
)


@pytest.mark.parametrize(
    ("stderr", "code"),
    [
        ("Sign in to confirm you’re not a bot", "YOUTUBE_AUTH_REQUIRED"),
        ("HTTP Error 429: Too Many Requests", "YOUTUBE_RATE_LIMITED"),
        ("This is a private video", "VIDEO_UNAVAILABLE"),
        ("Requested format is not available", "AUDIO_FORMAT_UNAVAILABLE"),
        ("File is larger than max-filesize", "SOURCE_TOO_LARGE"),
        ("HTTP Error 403", "YOUTUBE_DOWNLOAD_FAILED"),
    ],
)
def test_maps_public_errors(stderr, code):
    assert map_ytdlp_error(stderr).code == code


def test_selected_source_size_prefers_exact_requested_download():
    metadata = {
        "filesize_approx": 999,
        "requested_downloads": [{"filesize": 123, "filesize_approx": 456}],
    }
    assert selected_source_size(metadata) == 123


def test_selected_source_size_falls_back_and_handles_unknown():
    assert selected_source_size({"filesize_approx": 456}) == 456
    assert selected_source_size({"requested_downloads": [{}]}) is None


@pytest.mark.asyncio
async def test_metadata_rejects_oversized_selected_audio(monkeypatch):
    async def fake_process(args, **_kwargs):
        assert args[args.index("-f") + 1] == "bestaudio/best"
        return ProcessResult(
            stdout='{"duration": 30, "title": "fixture", "filesize": 11534336}',
            stderr="",
        )

    monkeypatch.setattr("app.services.youtube.run_process", fake_process)
    adapter = YouTubeAdapter(Settings(app_env="test", max_source_mb=10))
    url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    with pytest.raises(AppError) as error:
        await adapter.metadata(url)
    assert error.value.code == "SOURCE_TOO_LARGE"
