from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

StemBackend = Literal["demucs", "none"]
StemArtifactStatus = Literal["completed", "fallback", "skipped", "failed"]


class StemTaskStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "stems_queued"
    RUNNING = "stems_running"
    COMPLETED = "stems_completed"
    FALLBACK = "stems_fallback"
    SKIPPED = "stems_skipped"
    FAILED = "stems_failed"


class StemSeparationMetadata(BaseModel):
    status: StemArtifactStatus
    backend: StemBackend
    model: str | None = None
    device: str | None = None
    source_path: str
    vocals_path: str | None = None
    accompaniment_path: str | None = None
    duration_sec: float | None = None
    cached: bool = False
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
