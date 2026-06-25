from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from app.models.melody import (
    MelodyAnalysisResult,
    MelodyDebugMetadata,
    MelodyNote,
    MelodySource,
    MelodySourceUsed,
    MelodySummary,
    MeterHint,
)
from app.models.vocal_pitch import VocalPitchResult

_NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_DEGREE_LABELS = ("1", "#1", "2", "b3", "3", "4", "#4", "5", "b6", "6", "b7", "7")
_DEGREES = (1, 1, 2, 3, 3, 4, 4, 5, 6, 6, 7, 7)


def _librosa():
    os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/yt2mp3-numba-cache")
    import librosa

    return librosa


def _note_name(midi_note: int) -> str:
    return f"{_NOTE_NAMES[midi_note % 12]}{midi_note // 12 - 1}"


def _numbered_notation(midi_note: int, root_index: int) -> tuple[int, str]:
    pitch_delta = (midi_note - root_index) % 12
    tonic_midi = 60 + root_index
    octave_delta = math.floor((midi_note - tonic_midi) / 12)
    suffix = "'" * octave_delta if octave_delta > 0 else "," * abs(octave_delta)
    return _DEGREES[pitch_delta], _DEGREE_LABELS[pitch_delta] + suffix


def _beat_position(time_sec: float, beat_times: Any) -> float | None:
    import numpy as np

    if len(beat_times) < 2:
        return None
    index = int(np.searchsorted(beat_times, time_sec, side="right") - 1)
    index = max(0, min(index, len(beat_times) - 2))
    span = float(beat_times[index + 1] - beat_times[index])
    if span <= 0:
        return None
    return index + (time_sec - float(beat_times[index])) / span


def _meter_metadata(meter_hint: MeterHint) -> tuple[str, str | None]:
    if meter_hint in {"4/4", "3/4", "6/8"}:
        return meter_hint, meter_hint
    return "none", None


def _auto_meter_metadata(y: Any, sample_rate: int, hop_length: int, beat_frames: Any) -> tuple[str, str | None]:
    import numpy as np

    if len(beat_frames) < 8:
        return "none", None
    librosa = _librosa()
    onset = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop_length)
    beat_indices = np.asarray(beat_frames, dtype=int)
    beat_indices = beat_indices[(beat_indices >= 0) & (beat_indices < len(onset))]
    if len(beat_indices) < 8:
        return "none", None
    energies = np.asarray(onset[beat_indices], dtype=float)
    spread = float(np.std(energies))
    if not math.isfinite(spread) or spread < 1e-6:
        return "none", None

    candidates: list[tuple[str, float]] = []
    for meter, units in (("4/4", 4), ("3/4", 3), ("6/8", 6)):
        if len(energies) < units * 2:
            continue
        best_score = -999.0
        for phase in range(units):
            mask = np.arange(len(energies)) % units == phase
            if int(np.sum(mask)) < 2 or int(np.sum(~mask)) < 2:
                continue
            downbeat_mean = float(np.mean(energies[mask]))
            offbeat_mean = float(np.mean(energies[~mask]))
            best_score = max(best_score, (downbeat_mean - offbeat_mean) / spread)
        candidates.append((meter, best_score))

    if not candidates:
        return "none", None
    candidates.sort(key=lambda item: item[1], reverse=True)
    best_meter, best_score = candidates[0]
    second_score = candidates[1][1] if len(candidates) > 1 else -999.0
    if best_score >= 0.15 and best_score - second_score >= 0.05:
        return best_meter, best_meter
    return "none", None


def _resolve_meter_metadata(
    meter_hint: MeterHint,
    y: Any,
    sample_rate: int,
    hop_length: int,
    beat_frames: Any,
) -> tuple[str, str | None]:
    if meter_hint == "auto":
        return _auto_meter_metadata(y, sample_rate, hop_length, beat_frames)
    return _meter_metadata(meter_hint)


def _note_from_segment(
    *,
    note_id: str,
    start_sec: float,
    end_sec: float,
    midi_note: int,
    frequencies: list[float],
    confidences: list[float],
    beat_times: Any,
    meter_used: str,
    root_index: int,
    source: str,
) -> MelodyNote | None:
    if end_sec <= start_sec or not 0 <= midi_note <= 127:
        return None
    start_beat = _beat_position(start_sec, beat_times)
    end_beat = _beat_position(end_sec, beat_times)
    beat_scale = 3.0 if meter_used == "6/8" else 1.0
    beat_start = start_beat * beat_scale if start_beat is not None else None
    beat_duration = (
        (end_beat - start_beat) * beat_scale
        if start_beat is not None and end_beat is not None
        else None
    )
    quantized_start = round(beat_start * 4) / 4 if beat_start is not None else None
    quantized_duration = (
        max(0.25, round(beat_duration * 4) / 4) if beat_duration is not None else None
    )
    units_per_bar = {"4/4": 4, "3/4": 3, "6/8": 6}.get(meter_used)
    bar_index = (
        int(quantized_start // units_per_bar)
        if units_per_bar and quantized_start is not None and quantized_start >= 0
        else None
    )
    degree, notation = _numbered_notation(midi_note, root_index)
    return MelodyNote(
        note_id=note_id,
        start_sec=round(start_sec, 6),
        end_sec=round(end_sec, 6),
        duration_sec=round(end_sec - start_sec, 6),
        midi_note=midi_note,
        note_name=_note_name(midi_note),
        octave=midi_note // 12 - 1,
        frequency_hz=round(sum(frequencies) / len(frequencies), 3) if frequencies else None,
        beat_start=round(beat_start, 4) if beat_start is not None else None,
        beat_duration=round(beat_duration, 4) if beat_duration is not None else None,
        quantized_beat_start=quantized_start,
        quantized_beat_duration=quantized_duration,
        bar_index=bar_index,
        scale_degree=degree,
        numbered_notation=notation,
        confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0,
        source=source,
    )


def _write_result(
    json_output: Path,
    midi_output: Path,
    *,
    result: MelodyAnalysisResult,
) -> None:
    json_output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_midi(midi_output, result.notes, result.bpm)


def _notation_token(note: MelodyNote) -> str:
    return note.numbered_notation or note.note_name


def build_notation_lines(result: MelodyAnalysisResult, bars_per_line: int = 4) -> list[str]:
    """Build numbered notation as fixed four-bar lines when meter data exists."""
    if not result.notes:
        return []

    metered_notes = [note for note in result.notes if note.bar_index is not None]
    if result.meter_used != "none" and metered_notes:
        bars: dict[int, list[str]] = {}
        previous_by_bar: dict[int, MelodyNote] = {}
        for note in metered_notes:
            assert note.bar_index is not None
            bar_tokens = bars.setdefault(note.bar_index, [])
            previous = previous_by_bar.get(note.bar_index)
            if previous is not None and previous.beat_start is not None and note.beat_start is not None:
                previous_end = previous.beat_start + (previous.beat_duration or 0)
                gap_units = max(0.0, note.beat_start - previous_end)
                if gap_units >= 0.75:
                    bar_tokens.append("-" * min(4, max(1, round(gap_units))))
            bar_tokens.append(_notation_token(note))
            previous_by_bar[note.bar_index] = note

        first_bar = min(bars)
        last_bar = max(bars)
        lines: list[str] = []
        for start in range(first_bar, last_bar + 1, bars_per_line):
            parts = []
            for bar in range(start, start + bars_per_line):
                parts.append("".join(bars.get(bar, [])) or "-")
            lines.append("| " + " | ".join(parts) + " |")
        return lines

    tokens: list[str] = []
    previous: MelodyNote | None = None
    for note in result.notes:
        if previous is not None and note.start_sec - previous.end_sec >= 0.5:
            tokens.append("-")
        tokens.append(_notation_token(note))
        previous = note
    lines = []
    tokens_per_line = bars_per_line * 4
    for start in range(0, len(tokens), tokens_per_line):
        lines.append("| " + "".join(tokens[start : start + tokens_per_line]) + " |")
    return lines


def _write_midi(path: Path, notes: list[MelodyNote], bpm: float | None) -> None:
    import mido

    effective_bpm = bpm or 120.0
    ticks_per_beat = 480
    midi = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="yt2mp3 melody draft", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(effective_bpm), time=0))
    track.append(mido.Message("program_change", program=0, channel=0, time=0))

    events: list[tuple[int, int, str, int, int]] = []
    for note in notes:
        start_tick = round(note.start_sec * effective_bpm / 60 * ticks_per_beat)
        end_tick = max(start_tick + 1, round(note.end_sec * effective_bpm / 60 * ticks_per_beat))
        velocity = round(60 + note.confidence * 40)
        events.append((start_tick, 1, "note_on", note.midi_note, velocity))
        events.append((end_tick, 0, "note_off", note.midi_note, 0))
    previous_tick = 0
    for tick, _order, message_type, midi_note, velocity in sorted(events):
        track.append(
            mido.Message(
                message_type,
                note=midi_note,
                velocity=velocity,
                channel=0,
                time=max(0, tick - previous_tick),
            )
        )
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=ticks_per_beat if not events else 0))
    midi.save(path)


def _rmvpe_segments(
    pitch: VocalPitchResult,
    *,
    min_confidence: float,
    max_gap_merge_sec: float,
) -> tuple[list[tuple[float, float, int, list[float], list[float]]], float]:
    segments: list[tuple[float, float, int, list[float], list[float]]] = []
    current: tuple[float, float, int, list[float], list[float]] | None = None
    voiced_count = 0
    for point in pitch.points:
        if (
            not point.voiced
            or point.midi is None
            or point.frequency_hz is None
            or point.confidence < min_confidence
        ):
            continue
        voiced_count += 1
        midi_note = round(point.midi)
        if not 0 <= midi_note <= 127:
            continue
        start = float(point.time)
        end = start + pitch.hop_seconds
        if current is not None:
            first, last, current_note, frequencies, confidences = current
            gap = max(0.0, start - last)
            if current_note == midi_note and gap <= max_gap_merge_sec:
                frequencies.append(float(point.frequency_hz))
                confidences.append(float(point.confidence))
                current = (first, end, current_note, frequencies, confidences)
                continue
            segments.append(current)
        current = (
            start,
            end,
            midi_note,
            [float(point.frequency_hz)],
            [float(point.confidence)],
        )
    if current is not None:
        segments.append(current)
    voiced_ratio = voiced_count / len(pitch.points) if pitch.points else 0
    return segments, voiced_ratio


def analyze_rmvpe_melody(
    source: Path,
    vocal_pitch_json: Path,
    json_output: Path,
    midi_output: Path,
    *,
    job_id: str,
    key: str,
    root_index: int,
    mode: str,
    meter_hint: MeterHint,
    min_note_duration_sec: float,
    max_gap_merge_sec: float,
    min_confidence: float,
    max_notes: int,
    beat_reference: Path | None = None,
    requested_source: MelodySource = "vocals",
    melody_source_used: MelodySourceUsed = "vocals",
    source_audio_path: str = "analysis/stems/vocals.wav",
    separation_backend: str | None = None,
    separation_status: str = "missing",
) -> None:
    import numpy as np

    librosa = _librosa()
    pitch = VocalPitchResult.model_validate_json(vocal_pitch_json.read_text(encoding="utf-8"))
    beat_source = beat_reference or source
    y, sample_rate = librosa.load(beat_source, sr=None, mono=True)
    hop_length = 512
    tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sample_rate, hop_length=hop_length)
    tempo = float(np.asarray(tempo_raw).reshape(-1)[0]) if np.size(tempo_raw) else 0.0
    bpm = round(tempo, 3) if math.isfinite(tempo) and tempo > 0 and len(beat_frames) >= 2 else None
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)
    meter_used, time_signature = _resolve_meter_metadata(
        meter_hint, y, sample_rate, hop_length, beat_frames
    )

    segments, voiced_ratio = _rmvpe_segments(
        pitch,
        min_confidence=min_confidence,
        max_gap_merge_sec=max_gap_merge_sec,
    )
    notes: list[MelodyNote] = []
    truncated = False
    for start_sec, end_sec, midi_note, frequencies, confidences in segments:
        if end_sec - start_sec < min_note_duration_sec:
            continue
        note = _note_from_segment(
            note_id=f"n{len(notes) + 1:04d}",
            start_sec=start_sec,
            end_sec=end_sec,
            midi_note=midi_note,
            frequencies=frequencies,
            confidences=confidences,
            beat_times=beat_times,
            meter_used=meter_used,
            root_index=root_index,
            source="rmvpe_onnx",
        )
        if note:
            notes.append(note)
        if len(notes) >= max_notes:
            truncated = True
            break

    warnings = ["旋律由 RMVPE vocal pitch 產生，仍可能受 Demucs 分離 artifact 或和聲影響。"]
    if bpm is None:
        warnings.append("無法可靠估計 BPM；MIDI 使用 120 BPM。")
    if truncated:
        warnings.append(f"音符數已達上限 {max_notes}，後續候選音符未輸出。")
    if not notes:
        warnings.append("未找到符合可信度與最短音長條件的 RMVPE 旋律候選音符。")

    average_confidence = (
        round(sum(note.confidence for note in notes) / len(notes), 4) if notes else 0
    )
    average_note_duration = (
        round(sum(note.duration_sec for note in notes) / len(notes), 6) if notes else 0
    )
    octave_jump_count = sum(
        1
        for previous, current in zip(notes, notes[1:])
        if abs(current.midi_note - previous.midi_note) >= 12
    )
    result = MelodyAnalysisResult(
        job_id=job_id,
        algorithm_version="rmvpe-onnx-melody-v1",
        source_wav=source_audio_path,
        requested_source=requested_source,
        selected_source=melody_source_used,
        melody_source_used=melody_source_used,
        source_audio_path=source_audio_path,
        pitch_backend="rmvpe_onnx",
        separation_backend=separation_backend,
        separation_status=separation_status,
        is_fallback=False,
        key=key,
        mode=mode,
        bpm=bpm,
        meter_hint=meter_hint,
        meter_used=meter_used,
        time_signature=time_signature,
        notes=notes,
        summary=MelodySummary(
            note_count=len(notes),
            voiced_ratio=round(voiced_ratio, 4),
            average_confidence=average_confidence,
            estimated_range=(
                f"{_note_name(min(note.midi_note for note in notes))}-"
                f"{_note_name(max(note.midi_note for note in notes))}"
                if notes
                else None
            ),
            start_sec=notes[0].start_sec if notes else None,
            end_sec=notes[-1].end_sec if notes else None,
        ),
        debug_metadata=MelodyDebugMetadata(
            pitch_backend="rmvpe_onnx",
            source=melody_source_used,
            requested_source=requested_source,
            voiced_ratio=round(voiced_ratio, 4),
            note_count=len(notes),
            avg_note_duration=average_note_duration,
            octave_jump_count=octave_jump_count,
            confidence_threshold=min_confidence,
            voicing_threshold=pitch.voiced_confidence_threshold,
        ),
        warnings=warnings,
    )
    _write_result(json_output, midi_output, result=result)
