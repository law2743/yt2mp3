from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.models.music import KeyAnalysisResult, ShiftOption


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING_METADATA = "fetching_metadata"
    DOWNLOADING = "downloading"
    PREPARING_AUDIO = "preparing_audio"
    ANALYZING = "analyzing"
    READY = "ready"
    TRANSPOSING = "transposing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SourceInfo(BaseModel):
    video_id: str
    title: str
    uploader: str | None = None
    duration_seconds: int
    thumbnail_url: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class OutputInfo(BaseModel):
    semitones: int
    bitrate_kbps: Literal[128, 192, 256]


class JobPublic(BaseModel):
    job_id: str
    status: JobStatus
    stage: str
    progress: int = Field(ge=0, le=100)
    stage_progress: int | None = Field(default=None, ge=0, le=100)
    created_at: datetime
    expires_at: datetime
    source: SourceInfo | None = None
    analysis: KeyAnalysisResult | None = None
    shift_options: list[ShiftOption] | None = None
    outputs: list[OutputInfo] = Field(default_factory=list)
    active_shift: int | None = None
    active_bitrate_kbps: Literal[128, 192, 256] | None = None
    error: ErrorDetail | None = None
    features: dict[str, bool] = Field(default_factory=dict)
