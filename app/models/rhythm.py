from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.melody import MeterUsed, PitchBackend

RhythmPitchSource = PitchBackend | Literal[
    "hybrid_postprocessed",
    "fusion_postprocessed",
    "selected_midi",
    "manual",
    "unknown",
]
BoundarySource = Literal[
    "beat_grid",
    "vocal_onset",
    "pitch_change",
    "hybrid",
    "manual",
    "unknown",
]


class BeatEvent(BaseModel):
    beat_index: int = Field(ge=0)
    time_sec: float = Field(ge=0)
    beat_in_bar: int | None = Field(default=None, ge=1)
    bar_index: int | None = Field(default=None, ge=0)
    tempo_bpm: int | None = Field(default=None, gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class MeterHypothesis(BaseModel):
    meter: MeterUsed
    confidence: float = Field(ge=0, le=1)
    score: float = 0
    reason: str | None = None
    bpm: int | None = Field(default=None, gt=0)
    beats_per_bar: int | None = Field(default=None, ge=1)
    source: str | None = None


class BeatGridResult(BaseModel):
    schema_version: Literal["rhythm.beat_grid.v1"] = "rhythm.beat_grid.v1"
    backend: Literal["librosa"] = "librosa"
    algorithm_version: str
    source_audio_path: str = "analysis/stems/accompaniment.wav"
    duration_seconds: float = Field(ge=0)
    bpm: int | None = Field(default=None, gt=0)
    meter: MeterUsed = "none"
    meter_used: MeterUsed = "none"
    beats_per_bar: int | None = Field(default=None, ge=1)
    pulse_unit: str | None = None
    subdivision_unit: str | None = None
    subdivisions_per_beat: int | None = Field(default=None, ge=1)
    beat_times_sec: list[float] = Field(default_factory=list)
    bar_starts_sec: list[float] = Field(default_factory=list)
    beats: list[BeatEvent]
    meter_hypotheses: list[MeterHypothesis] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VocalOnsetEvent(BaseModel):
    onset_id: str
    time_sec: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    raw_score: float = Field(default=0, ge=0, le=1)
    backtracked_time_sec: float | None = Field(default=None, ge=0)
    source_backend: str = "librosa"
    is_primary: bool = True
    strength: float | None = Field(default=None, ge=0)
    source_audio_path: str = "analysis/stems/vocals.wav"
    warnings: list[str] = Field(default_factory=list)


class NoteDraft(BaseModel):
    note_id: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    duration_sec: float = Field(gt=0)
    midi_note: int = Field(ge=0, le=127)
    note_name: str | None = None
    frequency_hz: float | None = None
    raw_beat_start: float | None = None
    raw_beat_duration: float | None = None
    quantized_beat_start: float | None = None
    quantized_beat_duration: float | None = None
    bar_index: int | None = None
    scale_degree: int | None = None
    numbered_notation: str | None = None
    pitch_source: RhythmPitchSource = "hybrid_postprocessed"
    pitch_confidence: float | None = Field(default=None, ge=0, le=1)
    onset_confidence: float | None = Field(default=None, ge=0, le=1)
    quantization_confidence: float | None = Field(default=None, ge=0, le=1)
    boundary_source: BoundarySource = "unknown"
    boundary_reasons: list[str] = Field(default_factory=list)
    boundary_confidence: float | None = Field(default=None, ge=0, le=1)
    start_boundary_source: str | None = None
    end_boundary_source: str | None = None
    segment_frame_count: int | None = Field(default=None, ge=0)
    median_midi: float | None = Field(default=None, ge=0, le=127)
    pitch_stability_cents: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)


class RhythmDiagnostics(BaseModel):
    schema_version: Literal["rhythm.diagnostics.v1"] = "rhythm.diagnostics.v1"
    algorithm_version: str
    beat_backend: str | None = None
    onset_backend: str | None = None
    pitch_source: str | None = None
    meter_hypotheses: list[MeterHypothesis] = Field(default_factory=list)
    note_stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    beat_grid_path: str = "analysis/rhythm/beat_grid.json"
    vocal_onsets_path: str = "analysis/rhythm/vocal_onsets.csv"
    notes_draft_path: str = "analysis/rhythm/notes_draft.json"


class NoteDraftResult(BaseModel):
    schema_version: Literal["rhythm.notes_draft.v1"] = "rhythm.notes_draft.v1"
    algorithm_version: str
    pitch_source: RhythmPitchSource = "hybrid_postprocessed"
    beat_grid_source: str = "analysis/rhythm/beat_grid.json"
    onset_source: str | None = "analysis/rhythm/vocal_onsets.csv"
    bpm: float | None = Field(default=None, gt=0)
    meter_used: MeterUsed = "none"
    source_pitch_path: str = "analysis/melody/fusion/fusion.json"
    beat_grid_path: str = "analysis/rhythm/beat_grid.json"
    vocal_onsets_path: str = "analysis/rhythm/vocal_onsets.csv"
    notes: list[NoteDraft]
    diagnostics: RhythmDiagnostics | None = None
    warnings: list[str] = Field(default_factory=list)


class NumberedNotationNote(BaseModel):
    note_id: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    duration_sec: float = Field(ge=0)
    midi_note: int | None = Field(default=None, ge=0, le=127)
    note_name: str | None = None
    bar_index: int | None = Field(default=None, ge=0)
    raw_beat_start: float | None = None
    raw_beat_duration: float | None = None
    quantized_beat_start: float | None = None
    quantized_beat_duration: float | None = None
    scale_degree: str | None = None
    numbered_notation: str | None = None
    octave_offset: int | None = None
    accidental: str | None = None
    lyric: str | None = None
    warnings: list[str] = Field(default_factory=list)


class NumberedNotationBar(BaseModel):
    bar_index: int = Field(ge=0)
    notes: list[NumberedNotationNote] = Field(default_factory=list)


class NumberedNotationResult(BaseModel):
    schema_version: Literal["numbered_notation.v1"] = "numbered_notation.v1"
    source: Literal["notes_draft"] = "notes_draft"
    key: str | None = None
    mode: str | None = None
    meter_used: MeterUsed | None = None
    bpm: float | None = Field(default=None, gt=0)
    bars: list[NumberedNotationBar] = Field(default_factory=list)
    notes: list[NumberedNotationNote] = Field(default_factory=list)
    jianpu_text: str | None = None
    warnings: list[str] = Field(default_factory=list)
