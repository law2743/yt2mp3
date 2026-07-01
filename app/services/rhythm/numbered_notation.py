from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.models.rhythm import (
    NoteDraft,
    NoteDraftResult,
    NumberedNotationBar,
    NumberedNotationNote,
    NumberedNotationResult,
)

KEY_TO_PITCH_CLASS = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "FB": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    "CB": 11,
}

MAJOR_DEGREES = {
    0: ("1", None, "1"),
    1: ("#1", "#", "1"),
    2: ("2", None, "2"),
    3: ("#2", "#", "2"),
    4: ("3", None, "3"),
    5: ("4", None, "4"),
    6: ("#4", "#", "4"),
    7: ("5", None, "5"),
    8: ("#5", "#", "5"),
    9: ("6", None, "6"),
    10: ("#6", "#", "6"),
    11: ("7", None, "7"),
}

MINOR_DEGREES = {
    0: ("1", None, "1"),
    1: ("#1", "#", "1"),
    2: ("2", None, "2"),
    3: ("b3", "b", "3"),
    4: ("#3", "#", "3"),
    5: ("4", None, "4"),
    6: ("#4", "#", "4"),
    7: ("5", None, "5"),
    8: ("b6", "b", "6"),
    9: ("#6", "#", "6"),
    10: ("b7", "b", "7"),
    11: ("7", None, "7"),
}


def build_numbered_notation(
    notes_draft_path: Path,
    *,
    key: str | None = None,
    mode: str | None = None,
) -> NumberedNotationResult:
    payload = json.loads(notes_draft_path.read_text(encoding="utf-8"))
    draft = NoteDraftResult.model_validate(payload)
    warnings = list(draft.warnings)

    result_key = key or _optional_str(payload.get("key"))
    result_mode = mode or _optional_str(payload.get("mode"))
    if not result_key or not result_mode:
        result_key = result_key or "C"
        result_mode = result_mode or "major"
        warnings.append("missing_key_mode_used_c_major")

    normalized_mode = result_mode.lower()
    if normalized_mode not in {"major", "minor"}:
        warnings.append(f"unsupported_mode_used_major:{result_mode}")
        normalized_mode = "major"
    elif normalized_mode == "minor":
        warnings.append("minor_uses_natural_minor")

    tonic_pitch_class = _key_pitch_class(result_key)
    if tonic_pitch_class is None:
        warnings.append(f"unsupported_key_used_c:{result_key}")
        result_key = "C"
        tonic_pitch_class = 0

    notes = [
        _build_note(
            note,
            key_pitch_class=tonic_pitch_class,
            mode=normalized_mode,
            missing_bar_index=note.bar_index is None,
        )
        for note in draft.notes
    ]
    if any(note.bar_index is None for note in draft.notes):
        warnings.append("missing_bar_index")

    bars = (
        []
        if draft.meter_used == "none" or any(note.bar_index is None for note in draft.notes)
        else _group_bars(notes)
    )
    result = NumberedNotationResult(
        key=result_key,
        mode=normalized_mode,
        meter_used=draft.meter_used,
        bpm=draft.bpm,
        bars=bars,
        notes=notes,
        warnings=_unique(warnings),
    )
    result.jianpu_text = _build_jianpu_text(result)
    return result


def write_numbered_notation_json(
    result: NumberedNotationResult,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def write_jianpu_draft_txt(
    result: NumberedNotationResult,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text((result.jianpu_text or _build_jianpu_text(result)) + "\n", encoding="utf-8")


def _build_note(
    note: NoteDraft,
    *,
    key_pitch_class: int,
    mode: str,
    missing_bar_index: bool,
) -> NumberedNotationNote:
    note_warnings = list(note.warnings)
    numbered_notation: str | None = None
    octave_offset: int | None = None
    accidental: str | None = None
    scale_degree: str | None = None

    if note.midi_note is not None:
        numbered_notation, accidental, scale_degree = _midi_to_numbered(
            note.midi_note,
            key_pitch_class=key_pitch_class,
            mode=mode,
        )
        octave_offset = _octave_offset(note.midi_note, key_pitch_class)

    if _duration_beats(note) is None:
        note_warnings.append("missing_duration")

    return NumberedNotationNote(
        note_id=note.note_id,
        start_sec=note.start_sec,
        end_sec=note.end_sec,
        duration_sec=note.duration_sec,
        midi_note=note.midi_note,
        note_name=note.note_name,
        bar_index=None if missing_bar_index else note.bar_index,
        raw_beat_start=note.raw_beat_start,
        raw_beat_duration=note.raw_beat_duration,
        quantized_beat_start=note.quantized_beat_start,
        quantized_beat_duration=note.quantized_beat_duration,
        scale_degree=scale_degree,
        numbered_notation=numbered_notation,
        octave_offset=octave_offset,
        accidental=accidental,
        warnings=_unique(note_warnings),
    )


def _midi_to_numbered(
    midi_note: int,
    *,
    key_pitch_class: int,
    mode: str,
) -> tuple[str, str | None, str]:
    relative_pitch_class = (midi_note - key_pitch_class) % 12
    mapping = MINOR_DEGREES if mode == "minor" else MAJOR_DEGREES
    notation, accidental, scale_degree = mapping[relative_pitch_class]
    return notation, accidental, scale_degree


def _octave_offset(midi_note: int, key_pitch_class: int) -> int:
    tonic_midi = 60 + key_pitch_class
    return math.floor((midi_note - tonic_midi) / 12)


def _group_bars(notes: list[NumberedNotationNote]) -> list[NumberedNotationBar]:
    grouped: dict[int, list[NumberedNotationNote]] = {}
    for note in notes:
        grouped.setdefault(note.bar_index or 0, []).append(note)
    return [
        NumberedNotationBar(bar_index=bar_index, notes=bar_notes)
        for bar_index, bar_notes in sorted(grouped.items())
    ]


def _build_jianpu_text(result: NumberedNotationResult) -> str:
    lines = [
        f"Key: {result.key or ''}",
        f"Mode: {result.mode or ''}",
        f"Meter: {result.meter_used or ''}",
        f"BPM: {_display_bpm(result.bpm)}",
        "",
    ]
    if result.bars:
        bar_texts = [_bar_text(bar) for bar in result.bars]
        for index in range(0, len(bar_texts), 4):
            lines.append("| " + " | ".join(bar_texts[index : index + 4]) + " |")
    else:
        note_text = " ".join(_jianpu_note_text(note) for note in result.notes).strip()
        lines.append(f"| {note_text} |" if note_text else "| |")
    return "\n".join(lines)


def _bar_text(bar: NumberedNotationBar) -> str:
    return " ".join(_jianpu_note_text(note) for note in bar.notes).strip()


def _jianpu_note_text(note: NumberedNotationNote) -> str:
    token = note.numbered_notation or ""
    if note.octave_offset:
        token += _octave_marker(note.octave_offset)

    duration = _duration_beats(note)
    if duration is None:
        return token
    if _near(duration, 0.25):
        return f"{token}//"
    if _near(duration, 0.5):
        return f"{token}/"
    rounded = round(duration)
    if rounded >= 2 and _near(duration, rounded):
        return " ".join([token, *(["-"] * (rounded - 1))])
    return token


def _duration_beats(note: NoteDraft | NumberedNotationNote) -> float | None:
    duration = note.quantized_beat_duration
    if duration is None:
        duration = note.raw_beat_duration
    if duration is None or duration <= 0:
        return None
    return float(duration)


def _octave_marker(offset: int) -> str:
    if offset > 0:
        return "'" * offset
    return "," * abs(offset)


def _key_pitch_class(key: str) -> int | None:
    normalized = key.strip().upper().replace("♯", "#").replace("♭", "B")
    return KEY_TO_PITCH_CLASS.get(normalized)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _display_bpm(value: float | None) -> str:
    if value is None:
        return ""
    return str(round(value))


def _near(value: float, expected: float, tolerance: float = 0.08) -> bool:
    return abs(value - expected) <= tolerance


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
