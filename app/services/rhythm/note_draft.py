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
    "hybrid_postprocessed",
    "fusion_postprocessed",
    "selected_midi",
    "midi_note",
    "midi",
    "pitch_midi",
)
TIME_COLUMNS = ("time_sec", "time", "t")
FREQUENCY_COLUMNS = ("frequency_hz", "freq_hz", "f0_hz")
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


def build_note_draft(
    pitch_timeline_path: Path,
    beat_grid_path: Path,
    vocal_onsets_path: Path | None = None,
    *,
    min_note_duration_sec: float = 0.08,
    max_merge_gap_sec: float = 0.06,
    pitch_change_threshold_cents: float = 80.0,
    onset_boundary_tolerance_sec: float = 0.08,
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

    segments = _segment_pitch_frames(
        pitch.frames,
        onsets,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
        onset_boundary_tolerance_sec=onset_boundary_tolerance_sec,
    )
    segments = _drop_short_segments(segments, min_note_duration_sec)
    segments = _merge_close_segments(
        segments,
        max_merge_gap_sec=max_merge_gap_sec,
        pitch_change_threshold_cents=pitch_change_threshold_cents,
    )

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

        midi = _optional_float(row.get(pitch_column)) if pitch_column else None
        frequency_hz = _optional_float(row.get(frequency_column)) if frequency_column else None
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


def _segment_pitch_frames(
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
    current = Segment([frames[0]])
    for previous, frame in zip(frames, frames[1:], strict=False):
        gap = max(0.0, frame.time_sec - previous.time_sec)
        pitch_jump = abs(frame.midi - previous.midi) * 100.0
        if gap > max_merge_gap_sec or pitch_jump >= pitch_change_threshold_cents:
            boundary_source = "pitch_change"
            if _near_onset(frame.time_sec, onsets, onset_boundary_tolerance_sec) is not None:
                boundary_source = "vocal_onset"
            segments.append(current)
            current = Segment([frame], boundary_source=boundary_source)
            continue
        current.frames.append(frame)

    segments.append(current)
    return segments


def _drop_short_segments(segments: list[Segment], min_note_duration_sec: float) -> list[Segment]:
    return [segment for segment in segments if _segment_duration(segment) >= min_note_duration_sec]


def _merge_close_segments(
    segments: list[Segment],
    *,
    max_merge_gap_sec: float,
    pitch_change_threshold_cents: float,
) -> list[Segment]:
    if not segments:
        return []

    merged: list[Segment] = [segments[0]]
    for segment in segments[1:]:
        previous = merged[-1]
        gap = max(0.0, segment.frames[0].time_sec - _segment_end(previous))
        pitch_distance = abs(_segment_median_midi(previous) - _segment_median_midi(segment)) * 100.0
        if gap <= max_merge_gap_sec and pitch_distance < pitch_change_threshold_cents:
            previous.frames.extend(segment.frames)
            previous.warnings = [*(previous.warnings or []), "merged_short_gap_same_pitch"]
            continue
        merged.append(segment)
    return merged


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
    warnings = [*(segment.warnings or []), *quantization_warnings]

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
        warnings=warnings,
    )


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


def _first_existing_column(rows: list[dict[str, Any]], names: tuple[str, ...]) -> str | None:
    columns = {key for row in rows[:10] for key in row}
    return next((name for name in names if name in columns), None)


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
    if source in {"hybrid_postprocessed", "fusion_postprocessed", "selected_midi"}:
        return source
    return "hybrid_postprocessed" if source in {"midi_note", "midi", "pitch_midi"} else "unknown"


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"
