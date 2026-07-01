from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from app.models.melody import MeterUsed
from app.models.rhythm import BeatGridResult, NoteDraft, NoteDraftResult, RhythmDiagnostics

ALGORITHM_VERSION = "rhythm-note-draft-v1"
PITCH_COLUMN_PRIORITY = (
    "hybrid_postprocessed_midi",
    "hybrid_postprocessed_f0_hz",
    "fusion_postprocessed_midi",
    "fusion_postprocessed_f0_hz",
    "rmvpe_postprocessed_midi",
    "rmvpe_postprocessed_f0_hz",
    "hybrid_postprocessed",
    "fusion_postprocessed",
    "selected_midi",
    "midi_note",
    "midi",
    "pitch_midi",
)
TIME_COLUMNS = ("time_sec", "time", "t")
FREQUENCY_COLUMNS = (
    "hybrid_postprocessed_f0_hz",
    "fusion_postprocessed_f0_hz",
    "rmvpe_postprocessed_f0_hz",
    "frequency_hz",
    "freq_hz",
    "f0_hz",
)
CONFIDENCE_COLUMNS = ("confidence", "score")


@dataclass(frozen=True)
class PitchFrame:
    time_sec: float
    midi: float
    frequency_hz: float
    confidence: float | None


@dataclass(frozen=True)
class ParsedPitchTimeline:
    frames: list[PitchFrame]
    pitch_source: str
    warnings: list[str]


@dataclass(frozen=True)
class OnsetCandidate:
    time_sec: float
    confidence: float | None


@dataclass(frozen=True)
class BeatGridContext:
    beat_times_sec: list[float]
    bpm: int | None
    meter_used: MeterUsed
    beats_per_bar: int | None
    warnings: list[str]


@dataclass
class Segment:
    frames: list[PitchFrame]
    boundary_source: str = "hybrid"
    warnings: list[str] | None = None
    boundary_reasons: list[str] | None = None
    boundary_confidence: float | None = None
    start_boundary_source: str | None = None
    end_boundary_source: str | None = None
    end_boundary_reasons: list[str] | None = None
    end_boundary_confidence: float | None = None
    is_protected_short_note: bool = False
    is_octave_spike: bool = False
    is_below_min_subdivision: bool = False
    is_merge_candidate: bool = False


@dataclass(frozen=True)
class BoundaryDecision:
    should_split: bool
    confidence: float
    reasons: list[str]
    warnings: list[str]
    pitch_jump_cents: float | None = None
    gap_sec: float | None = None
    onset_distance_sec: float | None = None
    onset_confidence: float | None = None


@dataclass
class SegmentationStats:
    raw_segment_count: int = 0
    final_note_count: int = 0
    split_decision_count: int = 0
    strong_split_count: int = 0
    weak_split_count: int = 0
    suppressed_split_count: int = 0
    merged_same_pitch_count: int = 0
    removed_short_spike_count: int = 0
    removed_below_min_subdivision_count: int = 0
    removed_octave_spike_count: int = 0
    suppressed_short_ornament_count: int = 0
    kept_short_ornament_count: int = 0
    absorbed_short_spike_count: int = 0
    absorbed_below_min_subdivision_count: int = 0
    absorbed_octave_spike_count: int = 0
    protected_short_note_count: int = 0
    overmerge_guard_count: int = 0
    short_ornament_candidate_count: int = 0
    notes_before_cleanup: int = 0
    notes_after_cleanup: int = 0
    vocal_onset_boundary_count: int = 0
    pitch_jump_boundary_count: int = 0
    gap_boundary_count: int = 0
    same_pitch_onset_boundary_count: int = 0
    low_boundary_confidence_count: int = 0

    def as_note_stats(self) -> dict[str, int]:
        return {
            "raw_segment_count": self.raw_segment_count,
            "final_note_count": self.final_note_count,
            "split_decision_count": self.split_decision_count,
            "strong_split_count": self.strong_split_count,
            "weak_split_count": self.weak_split_count,
            "suppressed_split_count": self.suppressed_split_count,
            "merged_same_pitch_count": self.merged_same_pitch_count,
            "removed_short_spike_count": self.removed_short_spike_count,
            "removed_below_min_subdivision_count": self.removed_below_min_subdivision_count,
            "removed_octave_spike_count": self.removed_octave_spike_count,
            "suppressed_short_ornament_count": self.suppressed_short_ornament_count,
            "kept_short_ornament_count": self.kept_short_ornament_count,
            "absorbed_short_spike_count": self.absorbed_short_spike_count,
            "absorbed_below_min_subdivision_count": self.absorbed_below_min_subdivision_count,
            "absorbed_octave_spike_count": self.absorbed_octave_spike_count,
            "protected_short_note_count": self.protected_short_note_count,
            "overmerge_guard_count": self.overmerge_guard_count,
            "short_ornament_candidate_count": self.short_ornament_candidate_count,
            "notes_before_cleanup": self.notes_before_cleanup,
            "notes_after_cleanup": self.notes_after_cleanup,
            "vocal_onset_boundary_count": self.vocal_onset_boundary_count,
            "pitch_jump_boundary_count": self.pitch_jump_boundary_count,
            "gap_boundary_count": self.gap_boundary_count,
            "same_pitch_onset_boundary_count": self.same_pitch_onset_boundary_count,
            "low_boundary_confidence_count": self.low_boundary_confidence_count,
        }


def build_note_draft(
    pitch_timeline_path: Path,
    beat_grid_path: Path,
    vocal_onsets_path: Path | None = None,
    *,
    min_note_duration_sec: float = 0.08,
    max_merge_gap_sec: float = 0.06,
    pitch_change_threshold_cents: float = 80.0,
    onset_boundary_tolerance_sec: float = 0.08,
    strong_pitch_jump_cents: float = 160.0,
    weak_pitch_jump_cents: float = 80.0,
    same_pitch_cents: float = 60.0,
    vocal_onset_tolerance_sec: float = 0.08,
    short_spike_max_sec: float = 0.06,
    ornament_max_sec: float = 0.12,
    vibrato_suppression_cents: float = 120.0,
) -> NoteDraftResult:
    warnings: list[str] = []
    pitch = _load_pitch_timeline(pitch_timeline_path)
    warnings.extend(pitch.warnings)
    beat_grid = _load_beat_grid(beat_grid_path)
    warnings.extend(beat_grid.warnings)
    onsets = _load_vocal_onsets(vocal_onsets_path)

    diagnostics = RhythmDiagnostics(
        algorithm_version=ALGORITHM_VERSION,
        warnings=warnings,
        beat_grid_path=str(beat_grid_path),
        vocal_onsets_path=str(vocal_onsets_path) if vocal_onsets_path else "",
        notes_draft_path="analysis/rhythm/notes_draft.json",
    )

    if not pitch.frames:
        return NoteDraftResult(
            algorithm_version=ALGORITHM_VERSION,
            pitch_source=_result_pitch_source(pitch.pitch_source),
            beat_grid_source=str(beat_grid_path),
            onset_source=str(vocal_onsets_path) if vocal_onsets_path else None,
            bpm=beat_grid.bpm,
            meter_used=beat_grid.meter_used,
            source_pitch_path=str(pitch_timeline_path),
            beat_grid_path=str(beat_grid_path),
            vocal_onsets_path=str(vocal_onsets_path) if vocal_onsets_path else "",
            notes=[],
            diagnostics=diagnostics,
            warnings=warnings,
        )

    stats = SegmentationStats()
    try:
        segments = _segment_pitch_frames_with_boundary_decision(
            pitch.frames,
            onsets,
            max_merge_gap_sec=max_merge_gap_sec,
            pitch_change_threshold_cents=pitch_change_threshold_cents,
            onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
            strong_pitch_jump_cents=strong_pitch_jump_cents,
            weak_pitch_jump_cents=weak_pitch_jump_cents,
            same_pitch_cents=same_pitch_cents,
            vocal_onset_tolerance_sec=vocal_onset_tolerance_sec,
            vibrato_suppression_cents=vibrato_suppression_cents,
            stats=stats,
        )
    except Exception as exc:
        warnings.append(f"boundary_decision_failed_used_legacy:{type(exc).__name__}")
        segments = _segment_pitch_frames_legacy(
            pitch.frames,
            onsets,
            max_merge_gap_sec=max_merge_gap_sec,
            pitch_change_threshold_cents=pitch_change_threshold_cents,
            onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        )
        stats.raw_segment_count = len(segments)
    segments = _cleanup_segments_with_subdivision(
        segments,
        beat_grid,
        onsets,
        min_note_duration_sec=min_note_duration_sec,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
        same_pitch_cents=same_pitch_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        short_spike_max_sec=short_spike_max_sec,
        ornament_max_sec=ornament_max_sec,
        stats=stats,
    )
    stats.final_note_count = len(segments)
    diagnostics.note_stats.update(stats.as_note_stats())

    notes = [
        _segment_to_note(
            segment,
            index=index,
            pitch_source=_result_pitch_source(pitch.pitch_source),
            onsets=onsets,
            beat_grid=beat_grid,
        )
        for index, segment in enumerate(segments)
    ]

    return NoteDraftResult(
        algorithm_version=ALGORITHM_VERSION,
        pitch_source=_result_pitch_source(pitch.pitch_source),
        beat_grid_source=str(beat_grid_path),
        onset_source=str(vocal_onsets_path) if vocal_onsets_path else None,
        bpm=beat_grid.bpm,
        meter_used=beat_grid.meter_used,
        source_pitch_path=str(pitch_timeline_path),
        beat_grid_path=str(beat_grid_path),
        vocal_onsets_path=str(vocal_onsets_path) if vocal_onsets_path else "",
        notes=notes,
        diagnostics=diagnostics,
        warnings=warnings,
    )


def write_note_draft_csv(result: NoteDraftResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "note_id",
        "start_sec",
        "end_sec",
        "duration_sec",
        "midi_note",
        "note_name",
        "frequency_hz",
        "raw_beat_start",
        "raw_beat_duration",
        "quantized_beat_start",
        "quantized_beat_duration",
        "bar_index",
        "pitch_confidence",
        "onset_confidence",
        "quantization_confidence",
        "boundary_source",
        "boundary_reasons",
        "boundary_confidence",
        "segment_frame_count",
        "pitch_stability_cents",
        "warnings",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for note in result.notes:
            writer.writerow(
                {
                    "note_id": note.note_id,
                    "start_sec": _format_optional_float(note.start_sec),
                    "end_sec": _format_optional_float(note.end_sec),
                    "duration_sec": _format_optional_float(note.duration_sec),
                    "midi_note": note.midi_note,
                    "note_name": note.note_name or "",
                    "frequency_hz": _format_optional_float(note.frequency_hz),
                    "raw_beat_start": _format_optional_float(note.raw_beat_start),
                    "raw_beat_duration": _format_optional_float(note.raw_beat_duration),
                    "quantized_beat_start": _format_optional_float(note.quantized_beat_start),
                    "quantized_beat_duration": _format_optional_float(
                        note.quantized_beat_duration
                    ),
                    "bar_index": "" if note.bar_index is None else note.bar_index,
                    "pitch_confidence": _format_optional_float(note.pitch_confidence),
                    "onset_confidence": _format_optional_float(note.onset_confidence),
                    "quantization_confidence": _format_optional_float(
                        note.quantization_confidence
                    ),
                    "boundary_source": note.boundary_source,
                    "boundary_reasons": ";".join(getattr(note, "boundary_reasons", []) or []),
                    "boundary_confidence": _format_optional_float(
                        getattr(note, "boundary_confidence", None)
                    ),
                    "segment_frame_count": getattr(note, "segment_frame_count", None) or "",
                    "pitch_stability_cents": _format_optional_float(
                        getattr(note, "pitch_stability_cents", None)
                    ),
                    "warnings": ";".join(note.warnings),
                }
            )


def write_note_draft_json(result: NoteDraftResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _load_pitch_timeline(path: Path) -> ParsedPitchTimeline:
    if not path.exists():
        return ParsedPitchTimeline([], "unknown", ["missing_pitch_timeline"])

    try:
        rows = _read_pitch_rows(path)
    except Exception as exc:
        return ParsedPitchTimeline(
            [],
            "unknown",
            [f"pitch_timeline_read_failed:{type(exc).__name__}"],
        )

    if not rows:
        return ParsedPitchTimeline([], "unknown", ["empty_pitch_timeline"])

    pitch_column = _first_existing_column(rows, PITCH_COLUMN_PRIORITY)
    frequency_column = _first_existing_column(rows, FREQUENCY_COLUMNS)
    if pitch_column is None and frequency_column is None:
        return ParsedPitchTimeline([], "unknown", ["missing_pitch_column"])

    frames: list[PitchFrame] = []
    for index, row in enumerate(rows):
        time_sec = _first_float(row, TIME_COLUMNS, default=index * 0.01)
        if time_sec is None:
            continue

        pitch_value = _optional_float(row.get(pitch_column)) if pitch_column else None
        midi = None if _column_is_frequency(pitch_column) else pitch_value
        frequency_hz = _optional_float(row.get(frequency_column)) if frequency_column else None
        if frequency_hz is None and _column_is_frequency(pitch_column):
            frequency_hz = pitch_value
        if midi is None and frequency_hz is not None:
            midi = _hz_to_midi(frequency_hz)
        if frequency_hz is None and midi is not None:
            frequency_hz = _midi_to_hz(midi)
        if midi is None or frequency_hz is None:
            continue
        if not _is_valid_pitch(midi, frequency_hz):
            continue
        if _explicitly_unvoiced(row):
            continue

        confidence = _first_float(row, CONFIDENCE_COLUMNS)
        frames.append(
            PitchFrame(
                time_sec=float(time_sec),
                midi=float(midi),
                frequency_hz=float(frequency_hz),
                confidence=confidence,
            )
        )

    frames.sort(key=lambda frame: frame.time_sec)
    return ParsedPitchTimeline(frames, pitch_column or frequency_column or "unknown", [])


def _read_pitch_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            frames = payload.get("frames") or payload.get("points") or []
            if not isinstance(frames, list):
                return []
            return [row for row in frames if isinstance(row, dict)]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_beat_grid(path: Path) -> BeatGridContext:
    if not path.exists():
        return BeatGridContext([], None, "none", None, ["missing_or_insufficient_beat_grid"])

    try:
        result = BeatGridResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return BeatGridContext(
                [],
                None,
                "none",
                None,
                [f"beat_grid_read_failed:{type(exc).__name__}"],
            )
        beat_times = [_as_float(value) for value in payload.get("beat_times_sec", [])]
        beat_times = [value for value in beat_times if value is not None]
        meter_used = payload.get("meter_used") or payload.get("meter") or "none"
        return _beat_grid_context(
            beat_times,
            payload.get("bpm"),
            meter_used,
            payload.get("beats_per_bar"),
        )

    return _beat_grid_context(
        result.beat_times_sec,
        result.bpm,
        result.meter_used,
        result.beats_per_bar,
    )


def _beat_grid_context(
    beat_times: list[float],
    bpm: Any,
    meter_used: Any,
    beats_per_bar: Any,
) -> BeatGridContext:
    valid_beats = sorted(float(time) for time in beat_times if time >= 0)
    warnings = ["missing_or_insufficient_beat_grid"] if len(valid_beats) < 2 else []
    normalized_meter: MeterUsed = (
        meter_used if meter_used in {"none", "4/4", "3/4", "6/8"} else "none"
    )
    return BeatGridContext(
        valid_beats,
        _as_bpm(bpm),
        normalized_meter,
        int(beats_per_bar) if beats_per_bar else None,
        warnings,
    )


def _as_bpm(value: Any) -> int | None:
    parsed = _as_float(value)
    if parsed is None or parsed <= 0:
        return None
    return max(1, int(round(parsed)))


def _load_vocal_onsets(path: Path | None) -> list[OnsetCandidate]:
    if path is None or not path.exists():
        return []

    onsets: list[OnsetCandidate] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                time_sec = _optional_float(row.get("backtracked_time_sec"))
                if time_sec is None:
                    time_sec = _optional_float(row.get("time_sec"))
                if time_sec is None:
                    continue
                confidence = _optional_float(row.get("raw_score") or row.get("confidence"))
                onsets.append(OnsetCandidate(time_sec=time_sec, confidence=confidence))
    except Exception:
        return []
    return sorted(onsets, key=lambda onset: onset.time_sec)


def _segment_pitch_frames_with_boundary_decision(
    frames: list[PitchFrame],
    onsets: list[OnsetCandidate],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    onset_boundary_tolerance_sec: float,
    strong_pitch_jump_cents: float,
    weak_pitch_jump_cents: float,
    same_pitch_cents: float,
    vocal_onset_tolerance_sec: float,
    vibrato_suppression_cents: float,
    stats: SegmentationStats,
) -> list[Segment]:
    if not frames:
        return []

    segments: list[Segment] = []
    current = Segment([frames[0]], start_boundary_source="hybrid")
    for index, (previous, frame) in enumerate(zip(frames, frames[1:], strict=False), start=1):
        decision = _boundary_decision(
            frames,
            index,
            previous,
            frame,
            onsets,
            max_merge_gap_sec=max_merge_gap_sec,
            pitch_change_threshold_cents=pitch_change_threshold_cents,
            strong_pitch_jump_cents=strong_pitch_jump_cents,
            weak_pitch_jump_cents=weak_pitch_jump_cents,
            same_pitch_cents=same_pitch_cents,
            vocal_onset_tolerance_sec=vocal_onset_tolerance_sec,
            vibrato_suppression_cents=vibrato_suppression_cents,
        )
        if decision.should_split:
            stats.split_decision_count += 1
            _record_split_stats(stats, decision)
            boundary_source = _boundary_source_from_reasons(decision.reasons)
            current.end_boundary_source = boundary_source
            current.end_boundary_reasons = decision.reasons
            current.end_boundary_confidence = decision.confidence
            segments.append(current)
            current = Segment(
                [frame],
                boundary_source=boundary_source,
                warnings=decision.warnings,
                boundary_reasons=decision.reasons,
                boundary_confidence=decision.confidence,
                start_boundary_source=boundary_source,
            )
            continue
        if decision.warnings:
            if "vibrato_or_tail_drift_suppressed" in decision.warnings:
                stats.suppressed_split_count += 1
            current.warnings = [*(current.warnings or []), *decision.warnings]
        current.frames.append(frame)

    segments.append(current)
    segments = _split_unstable_segments_by_pitch_plateaus(segments, stats=stats)
    stats.raw_segment_count = len(segments)
    return segments


def _segment_pitch_frames(
    frames: list[PitchFrame],
    onsets: list[OnsetCandidate],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    onset_boundary_tolerance_sec: float,
    strong_pitch_jump_cents: float,
    weak_pitch_jump_cents: float,
    same_pitch_cents: float,
    vocal_onset_tolerance_sec: float,
    vibrato_suppression_cents: float,
    stats: SegmentationStats,
) -> list[Segment]:
    return _segment_pitch_frames_with_boundary_decision(
        frames,
        onsets,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        strong_pitch_jump_cents=strong_pitch_jump_cents,
        weak_pitch_jump_cents=weak_pitch_jump_cents,
        same_pitch_cents=same_pitch_cents,
        vocal_onset_tolerance_sec=vocal_onset_tolerance_sec,
        vibrato_suppression_cents=vibrato_suppression_cents,
        stats=stats,
    )


def _segment_pitch_frames_legacy(
    frames: list[PitchFrame],
    onsets: list[OnsetCandidate],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    onset_boundary_tolerance_sec: float,
) -> list[Segment]:
    if not frames:
        return []

    segments: list[Segment] = []
    current = Segment([frames[0]], start_boundary_source="hybrid")
    for previous, frame in zip(frames, frames[1:], strict=False):
        gap = max(0.0, frame.time_sec - previous.time_sec)
        pitch_jump = abs(frame.midi - previous.midi) * 100.0
        if gap > max_merge_gap_sec or pitch_jump >= pitch_change_threshold_cents:
            boundary_source = "pitch_change"
            if _near_onset(frame.time_sec, onsets, onset_boundary_tolerance_sec) is not None:
                boundary_source = "vocal_onset"
            current.end_boundary_source = boundary_source
            current.end_boundary_reasons = [boundary_source]
            current.end_boundary_confidence = 0.5
            segments.append(current)
            current = Segment(
                [frame],
                boundary_source=boundary_source,
                boundary_reasons=[boundary_source],
                boundary_confidence=0.5,
                start_boundary_source=boundary_source,
            )
            continue
        current.frames.append(frame)

    segments.append(current)
    return segments


def _boundary_decision(
    frames: list[PitchFrame],
    index: int,
    previous: PitchFrame,
    frame: PitchFrame,
    onsets: list[OnsetCandidate],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    strong_pitch_jump_cents: float,
    weak_pitch_jump_cents: float,
    same_pitch_cents: float,
    vocal_onset_tolerance_sec: float,
    vibrato_suppression_cents: float,
) -> BoundaryDecision:
    gap = max(0.0, frame.time_sec - previous.time_sec)
    pitch_jump = abs(frame.midi - previous.midi) * 100.0
    onset = _transition_onset(previous, frame, onsets, vocal_onset_tolerance_sec)
    onset_distance = abs(onset.time_sec - frame.time_sec) if onset else None
    onset_confidence = onset.confidence if onset else None
    before_stability, after_stability, local_distance = _local_pitch_context(frames, index)
    local_pitch_range = _pitch_spread_cents(
        frames[max(0, index - 3) : min(len(frames), index + 3)]
    )
    can_suppress_as_vibrato = (
        local_pitch_range <= 120.0
        or max(before_stability, after_stability) <= 150.0
    ) and local_pitch_range <= 250.0
    stable_different_pitch = (
        before_stability <= same_pitch_cents
        and after_stability <= same_pitch_cents
        and local_distance >= strong_pitch_jump_cents
    )
    likely_same_pitch = local_distance <= same_pitch_cents

    reasons: list[str] = []
    warnings: list[str] = []
    confidence = 0.0

    if gap > max_merge_gap_sec * 2:
        reasons.append("gap")
        confidence = max(confidence, 0.95)
    elif gap > max_merge_gap_sec:
        reasons.append("gap")
        confidence = max(confidence, 0.74)

    if pitch_jump >= strong_pitch_jump_cents:
        reasons.append("pitch_jump")
        confidence = max(confidence, 0.86)
    elif pitch_jump >= max(pitch_change_threshold_cents, 120.0):
        reasons.append("pitch_jump")
        confidence = max(confidence, 0.72)
    elif pitch_jump >= weak_pitch_jump_cents:
        if onset or stable_different_pitch:
            reasons.append("pitch_jump")
            confidence = max(confidence, 0.62)
        elif can_suppress_as_vibrato:
            warnings.append("vibrato_or_tail_drift_suppressed")
        elif local_pitch_range > 250.0 or local_distance > 250.0:
            reasons.append("large_pitch_transition")
            confidence = max(confidence, 0.62)

    if stable_different_pitch:
        reasons.append("stable_different_pitch")
        confidence = max(confidence, 0.82)

    if onset:
        if pitch_jump <= same_pitch_cents and likely_same_pitch:
            if (onset.confidence or 0.0) >= 0.85:
                reasons.append("vocal_onset_same_pitch")
                confidence = max(confidence, 0.70)
        elif pitch_jump >= weak_pitch_jump_cents or stable_different_pitch:
            reasons.append("vocal_onset")
            confidence = min(1.0, max(confidence + 0.10, 0.82))

    if pitch_jump < weak_pitch_jump_cents and not onset and not gap > max_merge_gap_sec:
        return BoundaryDecision(
            False,
            0.0,
            [],
            warnings,
            pitch_jump_cents=pitch_jump,
            gap_sec=gap,
            onset_distance_sec=onset_distance,
            onset_confidence=onset_confidence,
        )

    if (
        pitch_jump < vibrato_suppression_cents
        and not onset
        and gap <= max_merge_gap_sec
        and not stable_different_pitch
        and can_suppress_as_vibrato
    ):
        return BoundaryDecision(
            False,
            min(confidence, 0.35),
            reasons,
            _unique([*warnings, "vibrato_or_tail_drift_suppressed"]),
            pitch_jump_cents=pitch_jump,
            gap_sec=gap,
            onset_distance_sec=onset_distance,
            onset_confidence=onset_confidence,
        )

    should_split = confidence >= 0.60
    if should_split and confidence < 0.75:
        warnings.append("low_boundary_confidence")
    return BoundaryDecision(
        should_split,
        min(1.0, confidence),
        _unique(reasons),
        _unique(warnings),
        pitch_jump_cents=pitch_jump,
        gap_sec=gap,
        onset_distance_sec=onset_distance,
        onset_confidence=onset_confidence,
    )


def _split_unstable_segments_by_pitch_plateaus(
    segments: list[Segment],
    *,
    stats: SegmentationStats,
) -> list[Segment]:
    split_segments: list[Segment] = []
    for segment in segments:
        replacements = _try_split_unstable_segment_by_pitch_plateaus(segment)
        if len(replacements) > 1:
            stats.split_decision_count += len(replacements) - 1
            stats.pitch_jump_boundary_count += len(replacements) - 1
        split_segments.extend(replacements)
    return split_segments


def _try_split_unstable_segment_by_pitch_plateaus(segment: Segment) -> list[Segment]:
    if _segment_duration(segment) < 0.25 - 1e-9:
        return [segment]
    if _segment_pitch_stability_cents(segment) <= 250.0:
        return [segment]

    plateau_runs = _stable_pitch_plateau_runs(segment.frames)
    if len(plateau_runs) < 2:
        return [segment]

    split_indices: list[int] = []
    previous_run = plateau_runs[0]
    for run in plateau_runs[1:]:
        if abs(run["median_midi"] - previous_run["median_midi"]) * 100.0 >= 180.0:
            split_indices.append(int(run["start_index"]))
            previous_run = run

    split_indices = sorted({index for index in split_indices if 0 < index < len(segment.frames)})
    if not split_indices:
        return [segment]

    boundaries = [0, *split_indices, len(segment.frames)]
    warnings = [
        warning
        for warning in (segment.warnings or [])
        if warning != "vibrato_or_tail_drift_suppressed"
    ]
    replacements: list[Segment] = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        frames = segment.frames[start:end]
        if not frames:
            continue
        boundary_reasons = (
            segment.boundary_reasons
            if index == 0
            else ["intra_segment_pitch_plateau", "large_pitch_transition"]
        )
        replacement = Segment(
            frames=frames,
            boundary_source=segment.boundary_source if index == 0 else "pitch_change",
            warnings=warnings if index == 0 else ["large_pitch_drift_split"],
            boundary_reasons=boundary_reasons,
            boundary_confidence=segment.boundary_confidence if index == 0 else 0.82,
            start_boundary_source=(
                segment.start_boundary_source
                if index == 0
                else "pitch_change"
            ),
        )
        if index < len(boundaries) - 2:
            replacement.end_boundary_source = "pitch_change"
            replacement.end_boundary_reasons = [
                "intra_segment_pitch_plateau",
                "large_pitch_transition",
            ]
            replacement.end_boundary_confidence = 0.82
        else:
            replacement.end_boundary_source = segment.end_boundary_source
            replacement.end_boundary_reasons = segment.end_boundary_reasons
            replacement.end_boundary_confidence = segment.end_boundary_confidence
        replacements.append(replacement)

    return replacements if len(replacements) > 1 else [segment]


def _stable_pitch_plateau_runs(frames: list[PitchFrame]) -> list[dict[str, float | int]]:
    if not frames:
        return []

    runs: list[dict[str, float | int]] = []
    start_index = 0
    current_bin = _pitch_plateau_bin(frames[0].midi)
    for index, frame in enumerate(frames[1:], start=1):
        next_bin = _pitch_plateau_bin(frame.midi)
        if next_bin == current_bin:
            continue
        _append_stable_plateau_run(runs, frames, start_index, index)
        start_index = index
        current_bin = next_bin
    _append_stable_plateau_run(runs, frames, start_index, len(frames))
    return runs


def _append_stable_plateau_run(
    runs: list[dict[str, float | int]],
    frames: list[PitchFrame],
    start_index: int,
    end_index: int,
) -> None:
    run_frames = frames[start_index:end_index]
    if not run_frames:
        return
    run = Segment(run_frames)
    if _segment_duration(run) < 0.10 - 1e-9:
        return
    if _segment_pitch_stability_cents(run) > 120.0:
        return
    if _segment_pitch_confidence(run) < 0.70:
        return
    runs.append(
        {
            "start_index": start_index,
            "end_index": end_index,
            "median_midi": _segment_median_midi(run),
        }
    )


def _pitch_plateau_bin(midi: float) -> float:
    return round(midi * 2.0) / 2.0


def _drop_short_segments(segments: list[Segment], min_note_duration_sec: float) -> list[Segment]:
    if not segments:
        return []

    stats = SegmentationStats()
    return _cleanup_segments_with_subdivision(
        segments,
        BeatGridContext([], None, "none", None, []),
        [],
        min_note_duration_sec=min_note_duration_sec,
        max_merge_gap_sec=0.0,
        pitch_change_threshold_cents=0.0,
        same_pitch_cents=60.0,
        onset_boundary_tolerance_sec=0.0,
        short_spike_max_sec=min_note_duration_sec,
        ornament_max_sec=min_note_duration_sec,
        stats=stats,
    )


def _cleanup_segments_with_subdivision(
    segments: list[Segment],
    beat_grid: BeatGridContext,
    onsets: list[OnsetCandidate],
    *,
    min_note_duration_sec: float,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    same_pitch_cents: float,
    onset_boundary_tolerance_sec: float,
    short_spike_max_sec: float,
    ornament_max_sec: float,
    stats: SegmentationStats,
) -> list[Segment]:
    if not segments:
        return []

    subdivision_duration_sec = _subdivision_duration_sec(beat_grid)
    min_output_duration_sec = max(
        min_note_duration_sec,
        min(subdivision_duration_sec, ornament_max_sec),
    )
    stats.notes_before_cleanup = len(segments)
    _annotate_pre_cleanup_segment_flags(
        segments,
        onsets,
        min_output_duration_sec=min_output_duration_sec,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
        same_pitch_cents=same_pitch_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
    )
    cleaned: list[Segment] = []
    pending_absorbed_warning: str | None = None
    for index, segment in enumerate(segments):
        if pending_absorbed_warning:
            segment.warnings = _add_warning(segment.warnings, pending_absorbed_warning)
            _record_absorbed_warning(stats, pending_absorbed_warning)
            pending_absorbed_warning = None

        previous = cleaned[-1] if cleaned else None
        duration = _segment_duration(segment)
        onset = _near_segment_start_onset(
            segment,
            onsets,
            tolerance_sec=onset_boundary_tolerance_sec,
        )
        next_segment = _next_segment(segments, index)

        context = _short_segment_context(
            previous,
            segment,
            next_segment,
            max_merge_gap_sec=max_merge_gap_sec,
            same_pitch_cents=same_pitch_cents,
        )
        protected_short_note = segment.is_protected_short_note
        is_octave_spike = bool(segment.is_octave_spike or context["is_octave_spike"])

        if previous is not None and _should_merge_same_pitch_fragment(
            previous,
            segment,
            onsets,
            max_merge_gap_sec=max_merge_gap_sec,
            pitch_change_threshold_cents=pitch_change_threshold_cents,
            same_pitch_cents=same_pitch_cents,
            onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        ):
            if protected_short_note and _pitch_distance_cents(previous, segment) >= 150.0:
                stats.overmerge_guard_count += 1
                stats.protected_short_note_count += 1
                segment.warnings = _add_warning(segment.warnings, "protected_short_note")
                cleaned.append(segment)
                continue
            _merge_segment_into(previous, segment, warning="merged_same_pitch_fragment")
            stats.merged_same_pitch_count += 1
            continue

        if protected_short_note:
            stats.protected_short_note_count += 1
            stats.overmerge_guard_count += 1
            protected_warnings = ["protected_short_note"]
            if onset is not None and duration <= ornament_max_sec:
                stats.short_ornament_candidate_count += 1
                stats.kept_short_ornament_count += 1
                protected_warnings.extend(["short_ornament_candidate", "kept_short_ornament"])
            segment.warnings = _add_warnings(segment.warnings, protected_warnings)
            cleaned.append(segment)
            continue

        if duration + 1e-9 >= min_output_duration_sec:
            cleaned.append(segment)
            continue

        if (
            duration + 1e-9 >= min_note_duration_sec
            and onset is None
            and not context["bridges_same_pitch"]
            and not is_octave_spike
        ):
            cleaned.append(segment)
            continue

        if (
            duration + 1e-9 >= min_note_duration_sec
            and "vocal_onset_same_pitch" in (segment.boundary_reasons or [])
            and onset is not None
            and (onset.confidence or 0.0) >= 0.85
        ):
            cleaned.append(segment)
            continue

        if is_octave_spike:
            stats.removed_short_spike_count += 1
            stats.removed_octave_spike_count += 1
            pending_absorbed_warning = _absorb_short_segment(
                cleaned,
                segment,
                next_segment,
                warning="absorbed_octave_spike",
                stats=stats,
            )
            continue

        if duration <= short_spike_max_sec and onset is None and context["bridges_same_pitch"]:
            stats.removed_short_spike_count += 1
            pending_absorbed_warning = _absorb_short_segment(
                cleaned,
                segment,
                next_segment,
                warning="absorbed_short_spike",
                stats=stats,
            )
            continue

        if onset is not None and _should_keep_short_ornament(
            segment,
            onset,
            context,
            duration_sec=duration,
            min_output_duration_sec=min_output_duration_sec,
            ornament_max_sec=ornament_max_sec,
        ):
            stats.short_ornament_candidate_count += 1
            stats.kept_short_ornament_count += 1
            segment.warnings = _add_warnings(
                segment.warnings,
                ["short_ornament_candidate", "kept_short_ornament"],
            )
            cleaned.append(segment)
            continue

        if onset is not None:
            stats.suppressed_short_ornament_count += 1
            warning = "suppressed_short_ornament"
        else:
            stats.removed_below_min_subdivision_count += 1
            warning = "absorbed_below_min_subdivision"
        pending_absorbed_warning = _absorb_short_segment(
            cleaned,
            segment,
            next_segment,
            warning=warning,
            stats=stats,
        )

    if pending_absorbed_warning and cleaned:
        cleaned[-1].warnings = _add_warning(cleaned[-1].warnings, pending_absorbed_warning)
        _record_absorbed_warning(stats, pending_absorbed_warning)
    stats.notes_after_cleanup = len(cleaned)
    return cleaned


def _annotate_pre_cleanup_segment_flags(
    segments: list[Segment],
    onsets: list[OnsetCandidate],
    *,
    min_output_duration_sec: float,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    same_pitch_cents: float,
    onset_boundary_tolerance_sec: float,
) -> None:
    for index, segment in enumerate(segments):
        previous = segments[index - 1] if index > 0 else None
        next_segment = _next_segment(segments, index)
        duration = _segment_duration(segment)
        onset = _near_segment_start_onset(
            segment,
            onsets,
            tolerance_sec=onset_boundary_tolerance_sec,
        )
        context = _short_segment_context(
            previous,
            segment,
            next_segment,
            max_merge_gap_sec=max_merge_gap_sec,
            same_pitch_cents=same_pitch_cents,
        )

        segment.is_octave_spike = bool(context["is_octave_spike"])
        segment.is_below_min_subdivision = duration + 1e-9 < min_output_duration_sec
        segment.is_merge_candidate = (
            previous is not None
            and _should_merge_same_pitch_fragment(
                previous,
                segment,
                onsets,
                max_merge_gap_sec=max_merge_gap_sec,
                pitch_change_threshold_cents=pitch_change_threshold_cents,
                same_pitch_cents=same_pitch_cents,
                onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
            )
        )
        segment.is_protected_short_note = _is_protected_short_note(
            segment,
            onset,
            context,
            duration_sec=duration,
        )


def _classify_short_segments(
    segments: list[Segment],
    onsets: list[OnsetCandidate],
    *,
    min_note_duration_sec: float,
    max_merge_gap_sec: float,
    same_pitch_cents: float,
    onset_boundary_tolerance_sec: float,
    short_spike_max_sec: float,
    ornament_max_sec: float,
    stats: SegmentationStats,
) -> list[Segment]:
    return _cleanup_segments_with_subdivision(
        segments,
        BeatGridContext([], None, "none", None, []),
        onsets,
        min_note_duration_sec=min_note_duration_sec,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=same_pitch_cents,
        same_pitch_cents=same_pitch_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        short_spike_max_sec=short_spike_max_sec,
        ornament_max_sec=ornament_max_sec,
        stats=stats,
    )


def _merge_close_segments(
    segments: list[Segment],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    same_pitch_cents: float,
    onsets: list[OnsetCandidate],
    onset_boundary_tolerance_sec: float,
    stats: SegmentationStats,
) -> list[Segment]:
    return _cleanup_segments_with_subdivision(
        segments,
        BeatGridContext([], None, "none", None, []),
        onsets,
        min_note_duration_sec=0.0,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
        same_pitch_cents=same_pitch_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
        short_spike_max_sec=0.0,
        ornament_max_sec=0.0,
        stats=stats,
    )


def _subdivision_step_beats(meter_used: MeterUsed) -> float:
    return 1.0 / 3.0 if meter_used == "6/8" else 0.25


def _subdivision_duration_sec(beat_grid: BeatGridContext) -> float:
    if len(beat_grid.beat_times_sec) < 2:
        return 0.0
    intervals = [
        right - left
        for left, right in zip(beat_grid.beat_times_sec, beat_grid.beat_times_sec[1:], strict=False)
        if right > left
    ]
    if not intervals:
        return 0.0
    return float(median(intervals)) * _subdivision_step_beats(beat_grid.meter_used)


def _should_merge_same_pitch_fragment(
    previous: Segment,
    segment: Segment,
    onsets: list[OnsetCandidate],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
    same_pitch_cents: float,
    onset_boundary_tolerance_sec: float,
) -> bool:
    gap = max(0.0, segment.frames[0].time_sec - _segment_end(previous))
    pitch_distance = _pitch_distance_cents(previous, segment)
    start_onset = _near_onset(segment.frames[0].time_sec, onsets, onset_boundary_tolerance_sec)
    strong_onset_boundary = (
        not _previous_absorbed_short_segment(previous)
        and (
            "vocal_onset_same_pitch" in (segment.boundary_reasons or [])
            or (
                start_onset is not None
                and (start_onset.confidence or 0.0) >= 0.85
                and pitch_distance <= same_pitch_cents
            )
        )
    )
    return (
        gap <= max_merge_gap_sec
        and pitch_distance < pitch_change_threshold_cents
        and not strong_onset_boundary
    )


def _previous_absorbed_short_segment(previous: Segment) -> bool:
    warnings = previous.warnings or []
    return any(
        warning in warnings
        for warning in (
            "absorbed_short_spike",
            "absorbed_below_min_subdivision",
            "absorbed_octave_spike",
            "suppressed_short_ornament",
        )
    )


def _short_segment_context(
    previous: Segment | None,
    segment: Segment,
    next_segment: Segment | None,
    *,
    max_merge_gap_sec: float,
    same_pitch_cents: float,
) -> dict[str, bool | float | None]:
    previous_distance = _pitch_distance_cents(previous, segment) if previous else None
    next_distance = _pitch_distance_cents(segment, next_segment) if next_segment else None
    neighbor_distance = _pitch_distance_cents(previous, next_segment) if previous and next_segment else None
    duration = _segment_duration(segment)
    bridges_same_pitch = (
        previous is not None
        and next_segment is not None
        and neighbor_distance is not None
        and neighbor_distance <= same_pitch_cents
        and max(0.0, next_segment.frames[0].time_sec - _segment_end(previous))
        <= max_merge_gap_sec + duration + 0.02
    )
    is_octave_spike = (
        bridges_same_pitch
        and previous_distance is not None
        and next_distance is not None
        and _is_octave_distance(previous_distance)
        and _is_octave_distance(next_distance)
    )
    return {
        "previous_distance": previous_distance,
        "next_distance": next_distance,
        "neighbor_distance": neighbor_distance,
        "bridges_same_pitch": bridges_same_pitch,
        "is_octave_spike": is_octave_spike,
    }


def _should_keep_short_ornament(
    segment: Segment,
    onset: OnsetCandidate,
    context: dict[str, bool | float | None],
    *,
    duration_sec: float,
    min_output_duration_sec: float,
    ornament_max_sec: float,
) -> bool:
    if duration_sec > ornament_max_sec:
        return False
    if context["bridges_same_pitch"] or context["is_octave_spike"]:
        return False
    if (onset.confidence or 0.0) < 0.75:
        return False
    if duration_sec + 1e-9 >= min_output_duration_sec:
        return True

    distances = [
        value
        for value in (context["previous_distance"], context["next_distance"])
        if isinstance(value, float)
    ]
    if not distances:
        return False
    return min(distances) >= 120.0 and not any(_is_octave_distance(value) for value in distances)


def _is_protected_short_note(
    segment: Segment,
    onset: OnsetCandidate | None,
    context: dict[str, bool | float | None],
    *,
    duration_sec: float,
) -> bool:
    if len(segment.frames) < 2:
        return False
    if duration_sec > 0.30 + 1e-9:
        return False
    if duration_sec < (0.10 if onset is not None else 0.12) - 1e-9:
        return False
    if context["is_octave_spike"]:
        return False
    if _segment_pitch_confidence(segment) < 0.35:
        return False
    if _segment_pitch_stability_cents(segment) > 80.0:
        return False

    distances = [
        value
        for value in (context["previous_distance"], context["next_distance"])
        if isinstance(value, float)
    ]
    if not distances:
        return False

    if onset is not None:
        return min(distances) >= 150.0
    return min(distances) >= 180.0


def _absorb_short_segment(
    cleaned: list[Segment],
    segment: Segment,
    next_segment: Segment | None,
    *,
    warning: str,
    stats: SegmentationStats,
) -> str | None:
    if cleaned:
        cleaned[-1].warnings = _add_warning(cleaned[-1].warnings, warning)
        _record_absorbed_warning(stats, warning)
        return None
    if next_segment is not None:
        return warning
    segment.warnings = _add_warning(segment.warnings, warning)
    _record_absorbed_warning(stats, warning)
    cleaned.append(segment)
    return None


def _record_absorbed_warning(stats: SegmentationStats, warning: str) -> None:
    if warning == "absorbed_short_spike":
        stats.absorbed_short_spike_count += 1
    elif warning == "absorbed_below_min_subdivision":
        stats.absorbed_below_min_subdivision_count += 1
    elif warning == "absorbed_octave_spike":
        stats.absorbed_octave_spike_count += 1


def _merge_segment_into(previous: Segment, segment: Segment, *, warning: str) -> None:
    previous.frames.extend(segment.frames)
    previous.warnings = _add_warning(previous.warnings, warning)
    previous.end_boundary_source = segment.end_boundary_source
    previous.end_boundary_reasons = segment.end_boundary_reasons
    previous.end_boundary_confidence = segment.end_boundary_confidence


def _is_octave_distance(distance_cents: float) -> bool:
    return any(abs(distance_cents - target) <= 150.0 for target in (1200.0, 2400.0, 3600.0))


def _add_warning(warnings: list[str] | None, warning: str) -> list[str]:
    return _unique([*(warnings or []), warning])


def _add_warnings(warnings: list[str] | None, new_warnings: list[str]) -> list[str]:
    return _unique([*(warnings or []), *new_warnings])


def _segment_to_note(
    segment: Segment,
    *,
    index: int,
    pitch_source: str,
    onsets: list[OnsetCandidate],
    beat_grid: BeatGridContext,
) -> NoteDraft:
    start_sec = segment.frames[0].time_sec
    end_sec = _segment_end(segment)
    duration_sec = max(0.0, end_sec - start_sec)
    midi = int(round(_segment_median_midi(segment)))
    frequency_hz = median(frame.frequency_hz for frame in segment.frames)
    pitch_confidences = [
        frame.confidence for frame in segment.frames if frame.confidence is not None
    ]
    onset = _strongest_onset(start_sec, end_sec, onsets)
    raw_beat_start = _time_to_beat(start_sec, beat_grid.beat_times_sec)
    raw_beat_end = _time_to_beat(end_sec, beat_grid.beat_times_sec)
    raw_beat_duration = (
        max(0.0, raw_beat_end - raw_beat_start)
        if raw_beat_start is not None and raw_beat_end is not None
        else None
    )
    quantized_start, quantized_duration, quantization_confidence, quantization_warnings = (
        _quantize(raw_beat_start, raw_beat_duration, beat_grid.meter_used)
    )
    warnings = _sanitize_note_warnings(
        [*(segment.warnings or []), *quantization_warnings],
        duration_sec=duration_sec,
    )
    boundary_reasons = _unique(segment.boundary_reasons or [])

    return NoteDraft(
        note_id=f"note-{index + 1:04d}",
        start_sec=start_sec,
        end_sec=end_sec,
        duration_sec=duration_sec,
        midi_note=midi,
        note_name=_note_name(midi),
        frequency_hz=round(float(frequency_hz), 6),
        raw_beat_start=raw_beat_start,
        raw_beat_duration=raw_beat_duration,
        quantized_beat_start=quantized_start,
        quantized_beat_duration=quantized_duration,
        bar_index=_bar_index(raw_beat_start, beat_grid.beats_per_bar),
        pitch_source=pitch_source,
        pitch_confidence=(
            float(median(pitch_confidences)) if pitch_confidences else None
        ),
        onset_confidence=onset.confidence if onset else None,
        quantization_confidence=quantization_confidence,
        boundary_source=segment.boundary_source,
        boundary_reasons=boundary_reasons,
        boundary_confidence=segment.boundary_confidence,
        start_boundary_source=segment.start_boundary_source or segment.boundary_source,
        end_boundary_source=segment.end_boundary_source,
        segment_frame_count=len(segment.frames),
        median_midi=round(_segment_median_midi(segment), 6),
        pitch_stability_cents=round(_segment_pitch_stability_cents(segment), 6),
        warnings=_unique(warnings),
    )


def _sanitize_note_warnings(warnings: list[str], *, duration_sec: float) -> list[str]:
    result = _unique(warnings)
    if duration_sec > 0.30 + 1e-9:
        result = [
            warning
            for warning in result
            if warning not in {"protected_short_note", "short_ornament_candidate", "kept_short_ornament"}
        ]
    if "protected_short_note" in result:
        result = [
            warning
            for warning in result
            if warning not in {"removed_below_min_subdivision", "removed_octave_spike"}
        ]
    if "short_ornament_candidate" in result:
        result = [warning for warning in result if warning != "removed_short_spike"]
    return result


def _time_to_beat(time_sec: float, beat_times: list[float]) -> float | None:
    if len(beat_times) < 2:
        return None
    if time_sec < beat_times[0] or time_sec > beat_times[-1]:
        return None
    for index, (left, right) in enumerate(zip(beat_times, beat_times[1:], strict=False)):
        if left <= time_sec <= right:
            span = right - left
            if span <= 0:
                return float(index)
            return float(index + (time_sec - left) / span)
    return None


def _quantize(
    raw_beat_start: float | None,
    raw_beat_duration: float | None,
    meter_used: MeterUsed,
) -> tuple[float | None, float | None, float | None, list[str]]:
    if raw_beat_start is None or raw_beat_duration is None or meter_used == "none":
        return None, None, None, []

    step = 1.0 / 3.0 if meter_used == "6/8" else 0.25
    quantized_start = round(raw_beat_start / step) * step
    quantized_duration = max(step, round(raw_beat_duration / step) * step)
    error = abs(raw_beat_start - quantized_start) + abs(raw_beat_duration - quantized_duration)
    confidence = max(0.0, min(1.0, 1.0 - error / max(step, 1e-9)))
    if confidence < 0.5:
        return None, None, None, ["low_quantization_confidence"]
    return quantized_start, quantized_duration, confidence, []


def _bar_index(raw_beat_start: float | None, beats_per_bar: int | None) -> int | None:
    if raw_beat_start is None or not beats_per_bar:
        return None
    return int(math.floor(raw_beat_start / beats_per_bar))


def _segment_duration(segment: Segment) -> float:
    return _segment_end(segment) - segment.frames[0].time_sec


def _segment_end(segment: Segment) -> float:
    if len(segment.frames) >= 2:
        frame_step = segment.frames[-1].time_sec - segment.frames[-2].time_sec
    else:
        frame_step = 0.01
    return segment.frames[-1].time_sec + max(frame_step, 0.01)


def _segment_median_midi(segment: Segment) -> float:
    return float(median(frame.midi for frame in segment.frames))


def _segment_pitch_stability_cents(segment: Segment) -> float:
    if not segment.frames:
        return 0.0
    midis = [frame.midi for frame in segment.frames]
    return max(0.0, (max(midis) - min(midis)) * 100.0)


def _segment_pitch_confidence(segment: Segment) -> float:
    confidences = [
        frame.confidence for frame in segment.frames if frame.confidence is not None
    ]
    if not confidences:
        return 1.0
    return float(median(confidences))


def _pitch_distance_cents(left: Segment, right: Segment) -> float:
    return abs(_segment_median_midi(left) - _segment_median_midi(right)) * 100.0


def _next_segment(segments: list[Segment], index: int) -> Segment | None:
    next_index = index + 1
    if next_index >= len(segments):
        return None
    return segments[next_index]


def _local_pitch_context(frames: list[PitchFrame], index: int) -> tuple[float, float, float]:
    before = frames[max(0, index - 3) : index]
    after = frames[index : min(len(frames), index + 3)]
    before_stability = _pitch_spread_cents(before)
    after_stability = _pitch_spread_cents(after)
    if not before or not after:
        return before_stability, after_stability, 0.0
    local_distance = abs(median(frame.midi for frame in before) - median(frame.midi for frame in after))
    return before_stability, after_stability, float(local_distance) * 100.0


def _pitch_spread_cents(frames: list[PitchFrame]) -> float:
    if not frames:
        return 0.0
    midis = [frame.midi for frame in frames]
    return max(0.0, (max(midis) - min(midis)) * 100.0)


def _boundary_source_from_reasons(reasons: list[str]) -> str:
    if "vocal_onset" in reasons or "vocal_onset_same_pitch" in reasons:
        return "vocal_onset"
    if (
        "pitch_jump" in reasons
        or "stable_different_pitch" in reasons
        or "large_pitch_transition" in reasons
        or "intra_segment_pitch_plateau" in reasons
    ):
        return "pitch_change"
    if "gap" in reasons:
        return "hybrid"
    return "unknown"


def _record_split_stats(stats: SegmentationStats, decision: BoundaryDecision) -> None:
    if decision.confidence >= 0.85:
        stats.strong_split_count += 1
    else:
        stats.weak_split_count += 1
    if decision.confidence < 0.75:
        stats.low_boundary_confidence_count += 1
    if "vocal_onset" in decision.reasons or "vocal_onset_same_pitch" in decision.reasons:
        stats.vocal_onset_boundary_count += 1
    if "pitch_jump" in decision.reasons or "stable_different_pitch" in decision.reasons:
        stats.pitch_jump_boundary_count += 1
    if "gap" in decision.reasons:
        stats.gap_boundary_count += 1
    if "vocal_onset_same_pitch" in decision.reasons:
        stats.same_pitch_onset_boundary_count += 1


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _strongest_onset(
    start_sec: float,
    end_sec: float,
    onsets: list[OnsetCandidate],
) -> OnsetCandidate | None:
    candidates = [onset for onset in onsets if start_sec <= onset.time_sec <= end_sec]
    if not candidates:
        return None
    return max(candidates, key=lambda onset: onset.confidence or 0.0)


def _near_onset(
    time_sec: float,
    onsets: list[OnsetCandidate],
    tolerance_sec: float,
) -> OnsetCandidate | None:
    nearby = [onset for onset in onsets if abs(onset.time_sec - time_sec) <= tolerance_sec]
    if not nearby:
        return None
    return max(nearby, key=lambda onset: onset.confidence or 0.0)


def _near_segment_start_onset(
    segment: Segment,
    onsets: list[OnsetCandidate],
    *,
    tolerance_sec: float,
) -> OnsetCandidate | None:
    start_sec = segment.frames[0].time_sec
    duration_sec = _segment_duration(segment)
    forward_tolerance = min(tolerance_sec, max(duration_sec * 0.5, 0.01))
    candidates = [
        onset
        for onset in onsets
        if start_sec - 0.005 <= onset.time_sec <= start_sec + forward_tolerance
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda onset: onset.confidence or 0.0)


def _transition_onset(
    previous: PitchFrame,
    frame: PitchFrame,
    onsets: list[OnsetCandidate],
    tolerance_sec: float,
) -> OnsetCandidate | None:
    left = previous.time_sec
    hop = max(0.0, frame.time_sec - previous.time_sec)
    right = frame.time_sec + min(tolerance_sec, max(hop * 0.5, 0.005))
    candidates = [
        onset
        for onset in onsets
        if left < onset.time_sec <= right
        and abs(onset.time_sec - frame.time_sec) <= tolerance_sec
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda onset: onset.confidence or 0.0)


def _first_existing_column(rows: list[dict[str, Any]], names: tuple[str, ...]) -> str | None:
    columns = {key for row in rows[:10] for key in row}
    return next((name for name in names if name in columns), None)


def _column_is_frequency(column: str | None) -> bool:
    if column is None:
        return False
    lower = column.lower()
    return any(hint in lower for hint in ("f0", "hz", "freq", "frequency")) and "midi" not in lower


def _first_float(
    row: dict[str, Any],
    names: tuple[str, ...],
    *,
    default: float | None = None,
) -> float | None:
    for name in names:
        value = _optional_float(row.get(name))
        if value is not None:
            return value
    return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_float(value: Any) -> float | None:
    return _optional_float(value)


def _explicitly_unvoiced(row: dict[str, Any]) -> bool:
    voiced = row.get("voiced")
    if voiced is None or voiced == "":
        return False
    if isinstance(voiced, str):
        return voiced.strip().lower() in {"0", "false", "no", "n"}
    return not bool(voiced)


def _is_valid_pitch(midi: float, frequency_hz: float) -> bool:
    return (
        0 < frequency_hz
        and 0 < midi <= 127
        and math.isfinite(midi)
        and math.isfinite(frequency_hz)
    )


def _hz_to_midi(frequency_hz: float) -> float:
    return 69.0 + 12.0 * math.log2(frequency_hz / 440.0)


def _midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _note_name(midi_note: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi_note % 12]}{midi_note // 12 - 1}"


def _result_pitch_source(source: str) -> str:
    if source.startswith("hybrid_postprocessed"):
        return "hybrid_postprocessed"
    if source.startswith("fusion_postprocessed"):
        return "fusion_postprocessed"
    if source.startswith("rmvpe_postprocessed"):
        return "rmvpe"
    if source == "selected_midi":
        return source
    return "hybrid_postprocessed" if source in {"midi_note", "midi", "pitch_midi"} else "unknown"


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"
