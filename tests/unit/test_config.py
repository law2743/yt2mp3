import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_secrets():
    with pytest.raises(ValidationError):
        Settings(app_env="production", app_password=None)


def test_production_accepts_token_secret_and_exact_frontend_origin():
    settings = Settings(
        app_env="production",
        app_password="password",
        token_secret="x" * 32,
        cors_allowed_origins="https://frontend.example, http://localhost:5500/",
    )
    assert settings.allowed_origins == ["https://frontend.example", "http://localhost:5500"]


def test_cors_origin_rejects_wildcards_and_paths():
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            app_password="password",
            token_secret="x" * 32,
            cors_allowed_origins="*",
        )
    with pytest.raises(ValueError):
        Settings(cors_allowed_origins="https://frontend.example/path").allowed_origins


def test_shift_range_is_restricted():
    with pytest.raises(ValidationError):
        Settings(shift_range=4)


def test_empty_ytdlp_cookies_path_is_unset():
    assert Settings(ytdlp_cookies_file="").ytdlp_cookies_file is None
    assert Settings(ytdlp_cookies_file="# YouTube-DL cookies 檔案路徑（可留空）").ytdlp_cookies_file is None
