from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PitchPoint:
    """One normalized frame from one pitch backend."""

    backend: str
    time_sec: float
    f0_hz: float
    raw_f0_hz: float
    voiced: bool
    raw_confidence: float | None = None
    confidence_kind: str | None = None
    normalized_confidence: float = 0.0


@dataclass
class PitchTrack:
    """Normalized pitch track loaded from one backend CSV."""

    backend: str
    source_path: str
    sample_rate: int | None
    hop_size: int | None
    frame_step_sec: float
    frames: list[PitchPoint]
    original: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        if not self.frames:
            return 0.0
        return self.frames[-1].time_sec

    @property
    def frame_count(self) -> int:
        return len(self.frames)


@dataclass(frozen=True)
class Candidate:
    """Candidate state for one output frame."""

    f0_hz: float
    voiced: bool
    emission: float
    agreement: float
    support_count: int
    support_backends: tuple[str, ...]
    source_f0s: dict[str, float]


@dataclass
class FusionDiagnostics:
    """Compact diagnostics safe to store inside the fusion JSON."""

    input_backends: list[str]
    input_frame_counts: dict[str, int]
    input_durations_sec: dict[str, float]
    output_frame_count: int
    output_duration_sec: float
    timeline_mode: str
    model_weights: dict[str, float]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_backends": self.input_backends,
            "input_frame_counts": self.input_frame_counts,
            "input_durations_sec": self.input_durations_sec,
            "output_frame_count": self.output_frame_count,
            "output_duration_sec": self.output_duration_sec,
            "timeline_mode": self.timeline_mode,
            "model_weights": self.model_weights,
            "warnings": self.warnings,
        }
