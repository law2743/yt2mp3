from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MeterHint = Literal["auto", "none", "4/4", "3/4", "6/8"]
MeterUsed = Literal["none", "4/4", "3/4", "6/8"]
MelodySource = Literal["auto", "mix", "vocals"]
MelodySourceUsed = Literal["vocals"]
PitchBackend = Literal["rmvpe_onnx", "adaptive_fusion"]


class MelodyStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "melody_queued"
    PREPARING = "melody_preparing"
    DETECTING = "melody_extracting_pitch"
    EXPORTING = "melody_exporting"
    COMPLETED = "melody_completed"
    FAILED = "melody_failed"


class MelodyNote(BaseModel):
    note_id: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    duration_sec: float = Field(gt=0)
    midi_note: int = Field(ge=0, le=127)
    note_name: str
    octave: int
    frequency_hz: float | None = None
    beat_start: float | None = None
    beat_duration: float | None = None
    quantized_beat_start: float | None = None
    quantized_beat_duration: float | None = None
    bar_index: int | None = None
    scale_degree: int | None = None
    numbered_notation: str | None = None
    confidence: float = Field(ge=0, le=1)
    source: PitchBackend = "rmvpe_onnx"


class MelodySummary(BaseModel):
    note_count: int = Field(ge=0)
    voiced_ratio: float = Field(ge=0, le=1)
    average_confidence: float = Field(ge=0, le=1)
    estimated_range: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None


class MelodyDebugMetadata(BaseModel):
    pitch_backend: PitchBackend = "rmvpe_onnx"
    source: MelodySourceUsed = "vocals"
    requested_source: MelodySource = "auto"
    voiced_ratio: float = Field(ge=0, le=1)
    note_count: int = Field(ge=0)
    avg_note_duration: float = Field(ge=0)
    octave_jump_count: int = Field(ge=0)
    confidence_threshold: float = Field(ge=0, le=1)
    voicing_threshold: float = Field(ge=0, le=1)


class MelodyAnalysisResult(BaseModel):
    job_id: str
    status: Literal["completed"] = "completed"
    algorithm_version: str = "rmvpe-onnx-melody-v1"
    source_wav: str = "analysis/mono-22050.wav"
    requested_source: MelodySource = "auto"
    selected_source: MelodySourceUsed = "vocals"
    melody_source_used: MelodySourceUsed = "vocals"
    source_audio_path: str = "analysis/mono-22050.wav"
    pitch_backend: PitchBackend = "rmvpe_onnx"
    separation_backend: Literal["demucs", "none"] | None = None
    separation_status: str = "missing"
    is_fallback: bool = True
    key: str | None = None
    mode: Literal["major", "minor"] | None = None
    bpm: float | None = None
    meter_hint: MeterHint
    meter_used: MeterUsed = "none"
    time_signature: str | None = None
    notes: list[MelodyNote]
    summary: MelodySummary
    debug_metadata: MelodyDebugMetadata | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def backfill_selected_source(cls, value):
        if isinstance(value, dict) and "selected_source" not in value:
            value = {**value, "selected_source": value.get("melody_source_used", "vocals")}
        return value
