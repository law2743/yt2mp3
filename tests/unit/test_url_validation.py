import pytest

from app.errors import AppError
from app.services.youtube import canonicalize_youtube_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=10",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
    ],
)
def test_canonicalizes_supported_urls(url):
    result = canonicalize_youtube_url(url)
    assert result.video_id == "dQw4w9WgXcQ"
    assert result.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
        "https://www.youtube.com/channel/dQw4w9WgXcQ",
        "https://youtu.be/../../etc/passwd",
    ],
)
def test_rejects_unsafe_urls(url):
    with pytest.raises(AppError) as error:
        canonicalize_youtube_url(url)
    assert error.value.code == "INVALID_YOUTUBE_URL"


def test_playlist_has_specific_error():
    with pytest.raises(AppError) as error:
        canonicalize_youtube_url("https://youtube.com/watch?v=dQw4w9WgXcQ&list=abc")
    assert error.value.code == "PLAYLIST_NOT_SUPPORTED"
