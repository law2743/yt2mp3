from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_password: str | None = None
    token_secret: str = "development-only-token-secret-change-me"
    access_token_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    cors_allowed_origins: str = "http://127.0.0.1:5500,http://localhost:5500"
    login_max_failures: int = Field(default=5, ge=1, le=100)
    login_failure_window_seconds: int = Field(default=60, ge=10, le=3600)
    shift_range: int = 3
    job_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    max_video_duration_seconds: int = Field(default=900, ge=30, le=7200)
    max_source_mb: int = Field(default=150, ge=10, le=2048)
    max_queue_size: int = Field(default=5, ge=1, le=100)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=4)
    work_root: Path = Path("/tmp/yks")
    log_level: str = "INFO"
    ytdlp_cookies_file: Path | None = None
    metadata_timeout_seconds: int = 30
    download_timeout_seconds: int = 300
    analysis_timeout_seconds: int = 180
    transpose_timeout_seconds: int = 600
    enable_melody_analysis: bool = True
    melody_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    melody_min_note_duration_sec: float = Field(default=0.12, ge=0.02, le=2.0)
    melody_max_gap_merge_sec: float = Field(default=0.08, ge=0, le=1.0)
    melody_min_confidence: float = Field(default=0.45, ge=0, le=1)
    melody_fmin: str = "C2"
    melody_fmax: str = "C6"
    melody_max_notes: int = Field(default=2000, ge=1, le=10000)
    stem_separation_enabled: bool = False
    stem_separation_backend: Literal["auto", "demucs", "none"] = "auto"
    stem_separation_device: Literal["auto", "cuda", "cpu"] = "auto"
    demucs_python: Path = Path("/home/startech/venvs/yt2mp3-gpu/bin/python")
    demucs_model: str = "htdemucs"
    demucs_two_stems: Literal["vocals"] = "vocals"
    demucs_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    demucs_clean_env: bool = True
    rmvpe_python: Path = Path("/home/startech/venvs/yt2mp3-gpu/bin/python")
    rmvpe_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    rmvpe_voiced_confidence_threshold: float = Field(default=0.03, ge=0, le=1)
    allow_cpu_heavy_mode: bool = False
    stem_cache_enabled: bool = True
    melody_source_priority: str = "vocals,mix"

    @field_validator("shift_range")
    @classmethod
    def validate_shift_range(cls, value: int) -> int:
        if value not in (2, 3):
            raise ValueError("SHIFT_RANGE must be 2 or 3")
        return value

    @field_validator("ytdlp_cookies_file", mode="before")
    @classmethod
    def empty_cookies_path_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("demucs_model")
    @classmethod
    def validate_demucs_model(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100 or not all(c.isalnum() or c in "._-" for c in value):
            raise ValueError("DEMUCS_MODEL contains unsupported characters")
        return value

    @field_validator("melody_source_priority")
    @classmethod
    def validate_melody_source_priority(cls, value: str) -> str:
        sources = [item.strip() for item in value.split(",") if item.strip()]
        if set(sources) != {"vocals", "mix"} or len(sources) != 2:
            raise ValueError("MELODY_SOURCE_PRIORITY must contain vocals,mix once each")
        return ",".join(sources)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if (
            self.stem_separation_enabled
            and self.stem_separation_backend != "none"
            and not self.demucs_clean_env
        ):
            raise ValueError("DEMUCS_CLEAN_ENV must be true when Demucs is enabled")
        if self.app_env == "production":
            if not self.app_password:
                raise ValueError("APP_PASSWORD is required in production")
            if len(self.token_secret) < 32 or self.token_secret.startswith("development-"):
                raise ValueError("TOKEN_SECRET must contain at least 32 non-default characters")
            if not self.allowed_origins:
                raise ValueError("CORS_ALLOWED_ORIGINS is required in production")
        return self

    @property
    def authentication_enabled(self) -> bool:
        return self.app_env == "production" or bool(self.app_password)

    @property
    def allowed_origins(self) -> list[str]:
        origins: list[str] = []
        for raw_origin in self.cors_allowed_origins.split(","):
            origin = raw_origin.strip().rstrip("/")
            if not origin:
                continue
            parsed = urlsplit(origin)
            if origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("CORS_ALLOWED_ORIGINS must contain comma-separated HTTP origins")
            if parsed.path or parsed.query or parsed.fragment:
                raise ValueError("CORS_ALLOWED_ORIGINS entries cannot contain paths")
            origins.append(origin)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
