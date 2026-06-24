from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VocalPitchPoint(BaseModel):
    time: float = Field(ge=0)
    frequency_hz: float | None = None
    midi: float | None = None
    confidence: float = Field(ge=0, le=1)
    voiced: bool


class VocalPitchMetadata(BaseModel):
    model: str
    device: str
    confidence_source: Literal["rmvpe_onnx"] = "rmvpe_onnx"
    activation_shape: list[int] | None = None
    created_at: str


class VocalPitchResult(BaseModel):
    schema_version: Literal["vocal_pitch.v1"] = "vocal_pitch.v1"
    backend: Literal["rmvpe_onnx"] = "rmvpe_onnx"
    fallback_used: Literal[False] = False
    input_source: Literal["vocals"] = "vocals"
    sample_rate: int = Field(gt=0)
    duration_seconds: float = Field(ge=0)
    frame_hz: float = Field(gt=0)
    hop_seconds: float = Field(gt=0)
    voiced_confidence_threshold: float = Field(ge=0, le=1)
    points: list[VocalPitchPoint]
    metadata: VocalPitchMetadata
