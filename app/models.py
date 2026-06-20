from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


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


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048, strict=True)


class TransposeRequest(BaseModel):
    semitones: int = Field(strict=True)
    bitrate_kbps: Literal[128, 192, 256] = 192


class KeyCandidate(BaseModel):
    key: str
    score: float = Field(ge=0, le=1)


class KeyAnalysisResult(BaseModel):
    root_index: int = Field(ge=0, le=11)
    root_name: str
    mode: Literal["major", "minor"]
    display_name: str
    confidence: float = Field(ge=0, le=1)
    candidates: list[KeyCandidate] = Field(max_length=3)
    algorithm_version: str

    @computed_field
    @property
    def key(self) -> str:
        return self.display_name


class SourceInfo(BaseModel):
    video_id: str
    title: str
    uploader: str | None = None
    duration_seconds: int
    thumbnail_url: str | None = None


class ShiftOption(BaseModel):
    semitones: int
    label: str
    target_key: str


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
