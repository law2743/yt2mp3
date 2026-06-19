from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_password: str | None = None
    session_secret: str = "development-only-secret-change-me"
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

    @field_validator("shift_range")
    @classmethod
    def validate_shift_range(cls, value: int) -> int:
        if value not in (2, 3):
            raise ValueError("SHIFT_RANGE must be 2 or 3")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if not self.app_password:
                raise ValueError("APP_PASSWORD is required in production")
            if len(self.session_secret) < 32 or self.session_secret.startswith("development-"):
                raise ValueError("SESSION_SECRET must contain at least 32 non-default characters")
        return self

    @property
    def authentication_enabled(self) -> bool:
        return self.app_env == "production" or bool(self.app_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()

