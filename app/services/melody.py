from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.models.melody import (
    MelodyAnalysisResult,
    MelodyNote,
    MelodySourceUsed,
    MelodySummary,
    MeterHint,
)

_NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
_DEGREE_LABELS = ("1", "#1", "2", "b3", "3", "4", "#4", "5", "b6", "6", "b7", "7")
_DEGREES = (1, 1, 2, 3, 3, 4, 4, 5, 6, 6, 7, 7)


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
    # Phase 2A deliberately does not claim automatic meter recognition.
    if meter_hint in {"4/4", "3/4", "6/8"}:
        return meter_hint, meter_hint
    return "none", None


def build_notation_lines(result: MelodyAnalysisResult, max_tokens: int = 16) -> list[str]:
    """Build a compact draft preview without inventing notes to fill a bar."""
    tokens: list[str] = []
    previous: MelodyNote | None = None
    for note in result.notes:
        if previous is not None:
            gap_sec = max(0.0, note.start_sec - previous.end_sec)
            if previous.beat_start is not None and previous.beat_duration is not None:
                previous_end = previous.beat_start + previous.beat_duration
                gap_units = max(0.0, (note.beat_start or previous_end) - previous_end)
                if gap_units >= 0.75:
                    tokens.extend("-" for _ in range(min(4, max(1, round(gap_units)))))
            elif gap_sec >= 0.5:
                tokens.append("-")

            if (
                result.meter_used != "none"
                and previous.bar_index is not None
                and note.bar_index is not None
                and note.bar_index > previous.bar_index
            ):
                tokens.append("|")
            elif result.meter_used == "6/8" and note.quantized_beat_start is not None:
                position = note.quantized_beat_start % 6
                previous_position = (
                    previous.quantized_beat_start % 6
                    if previous.quantized_beat_start is not None
                    else None
                )
                if previous_position is not None and previous_position < 3 <= position:
                    tokens.append("/")
        tokens.append(note.numbered_notation or note.note_name)
        previous = note

    lines: list[str] = []
    for start in range(0, len(tokens), max_tokens):
        lines.append(" ".join(tokens[start : start + max_tokens]))
    return lines


def _write_midi(path: Path, notes: list[MelodyNote], bpm: float | None) -> None:
    import mido

    effective_bpm = bpm or 120.0
    ticks_per_beat = 480
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="yt2mp3 melody draft", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(effective_bpm), time=0))

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
                time=max(0, tick - previous_tick),
            )
        )
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.save(path)


def segment_pitch_frames(
    f0: Any,
    voiced_flag: Any,
    voiced_probability: Any,
    *,
    frame_seconds: float,
    min_confidence: float,
    max_gap_merge_sec: float,
) -> tuple[list[tuple[int, int, int, list[float], list[float]]], Any]:
    """Convert pYIN frames into stable semitone segments."""
    import librosa
    import numpy as np

    probabilities = np.nan_to_num(voiced_probability, nan=0.0)
    valid = voiced_flag & np.isfinite(f0) & (probabilities >= min_confidence)
    midi_frames = np.rint(librosa.hz_to_midi(f0)).astype(float)
    segments: list[tuple[int, int, int, list[float], list[float]]] = []
    current: tuple[int, int, int, list[float], list[float]] | None = None
    for index_value in np.flatnonzero(valid):
        index = int(index_value)
        midi_note = int(midi_frames[index])
        frequency = float(f0[index])
        confidence = float(probabilities[index])
        if current is not None:
            first, last, current_note, frequencies, confidences = current
            gap = (index - last - 1) * frame_seconds
            if current_note == midi_note and gap <= max_gap_merge_sec:
                frequencies.append(frequency)
                confidences.append(confidence)
                current = (first, index, current_note, frequencies, confidences)
                continue
            segments.append(current)
        current = (index, index, midi_note, [frequency], [confidence])
    if current is not None:
        segments.append(current)
    return segments, valid


def analyze_melody(
    source: Path,
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
    fmin: str,
    fmax: str,
    max_notes: int,
    melody_source_used: MelodySourceUsed = "mix",
    source_audio_path: str = "analysis/mono-22050.wav",
    separation_backend: str | None = None,
    separation_status: str = "missing",
) -> None:
    import librosa
    import numpy as np

    y, sample_rate = librosa.load(source, sr=None, mono=True)
    hop_length = 512
    tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sample_rate, hop_length=hop_length)
    tempo = float(np.asarray(tempo_raw).reshape(-1)[0]) if np.size(tempo_raw) else 0.0
    bpm = round(tempo, 3) if math.isfinite(tempo) and tempo > 0 and len(beat_frames) >= 2 else None
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)

    f0, voiced_flag, voiced_probability = librosa.pyin(
        y,
        fmin=float(librosa.note_to_hz(fmin)),
        fmax=float(librosa.note_to_hz(fmax)),
        sr=sample_rate,
        frame_length=2048,
        hop_length=hop_length,
    )
    frame_seconds = hop_length / sample_rate
    segments, valid = segment_pitch_frames(
        f0,
        voiced_flag,
        voiced_probability,
        frame_seconds=frame_seconds,
        min_confidence=min_confidence,
        max_gap_merge_sec=max_gap_merge_sec,
    )

    meter_used, time_signature = _meter_metadata(meter_hint)
    notes: list[MelodyNote] = []
    truncated = False
    for first, last, midi_note, frequencies, confidences in segments:
        start_sec = first * frame_seconds
        end_sec = (last + 1) * frame_seconds
        duration_sec = end_sec - start_sec
        if duration_sec < min_note_duration_sec or not 0 <= midi_note <= 127:
            continue
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
        notes.append(
            MelodyNote(
                note_id=f"n{len(notes) + 1:04d}",
                start_sec=round(start_sec, 6),
                end_sec=round(end_sec, 6),
                duration_sec=round(duration_sec, 6),
                midi_note=midi_note,
                note_name=_note_name(midi_note),
                octave=midi_note // 12 - 1,
                frequency_hz=round(float(np.mean(frequencies)), 3),
                beat_start=round(beat_start, 4) if beat_start is not None else None,
                beat_duration=round(beat_duration, 4) if beat_duration is not None else None,
                quantized_beat_start=quantized_start,
                quantized_beat_duration=quantized_duration,
                bar_index=bar_index,
                scale_degree=degree,
                numbered_notation=notation,
                confidence=round(float(np.mean(confidences)), 4),
            )
        )
        if len(notes) >= max_notes:
            truncated = True
            break

    warnings = ["此為 CPU-only pYIN 旋律預覽，不代表正式樂譜或準確扒譜結果。"]
    if melody_source_used == "mix":
        warnings.append("旋律由完整混音產生，可能包含伴奏、Bass 或和聲干擾。")
    else:
        warnings.append("旋律由人聲 stem 產生；分離 artifact 或和聲仍可能影響結果。")
    if bpm is None:
        warnings.append("無法可靠估計 BPM；MIDI 使用 120 BPM。")
    if meter_hint == "auto":
        warnings.append("Phase 2A 未自動判定拍號，meter_used 已回退為 none。")
    if truncated:
        warnings.append(f"音符數已達上限 {max_notes}，後續候選音符未輸出。")
    if not notes:
        warnings.append("未找到符合可信度與最短音長條件的旋律候選音符。")

    result = MelodyAnalysisResult(
        job_id=job_id,
        source_wav=source_audio_path,
        melody_source_used=melody_source_used,
        source_audio_path=source_audio_path,
        separation_backend=separation_backend,
        separation_status=separation_status,
        is_fallback=True,
        key=key,
        mode=mode,
        bpm=bpm,
        meter_hint=meter_hint,
        meter_used=meter_used,
        time_signature=time_signature,
        notes=notes,
        summary=MelodySummary(
            note_count=len(notes),
            voiced_ratio=round(float(np.mean(valid)), 4) if len(valid) else 0,
            average_confidence=(
                round(sum(note.confidence for note in notes) / len(notes), 4) if notes else 0
            ),
            estimated_range=(
                f"{_note_name(min(note.midi_note for note in notes))}-"
                f"{_note_name(max(note.midi_note for note in notes))}"
                if notes
                else None
            ),
            start_sec=notes[0].start_sec if notes else None,
            end_sec=notes[-1].end_sec if notes else None,
        ),
        warnings=warnings,
    )
    json_output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_midi(midi_output, notes, bpm)
