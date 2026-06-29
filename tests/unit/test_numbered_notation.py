from __future__ import annotations

import json

from app.models.rhythm import NoteDraft, NoteDraftResult
from app.services.artifacts import JobArtifacts
from app.services.rhythm.numbered_notation import (
    build_numbered_notation,
    write_jianpu_draft_txt,
    write_numbered_notation_json,
)


def _note(
    midi_note: int,
    *,
    note_id: str = "note-0001",
    bar_index: int | None = 0,
    quantized_beat_duration: float | None = 1.0,
) -> NoteDraft:
    return NoteDraft(
        note_id=note_id,
        start_sec=0.0,
        end_sec=0.5,
        duration_sec=0.5,
        midi_note=midi_note,
        bar_index=bar_index,
        quantized_beat_start=0.0,
        quantized_beat_duration=quantized_beat_duration,
    )


def _write_notes_draft(path, notes, *, key="C", mode="major") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = NoteDraftResult(
        algorithm_version="test",
        bpm=82.0,
        meter_used="4/4",
        notes=notes,
    )
    payload = result.model_dump()
    if key is not None:
        payload["key"] = key
    if mode is not None:
        payload["mode"] = mode
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_c_major_natural_notes_convert_to_numbered_notation(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    notes = [
        _note(midi, note_id=f"note-{index:04d}")
        for index, midi in enumerate([60, 62, 64, 65, 67, 69, 71], start=1)
    ]
    _write_notes_draft(path, notes)

    result = build_numbered_notation(path)

    assert [note.numbered_notation for note in result.notes] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]


def test_sharp_accidental_can_be_represented(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(path, [_note(61)])

    result = build_numbered_notation(path)

    assert result.notes[0].numbered_notation == "#1"
    assert result.notes[0].accidental == "#"


def test_octave_markers_are_added_to_jianpu_text(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(
        path,
        [
            _note(72, note_id="note-high"),
            _note(48, note_id="note-low"),
        ],
    )

    result = build_numbered_notation(path)

    assert [note.octave_offset for note in result.notes] == [1, -1]
    assert "| 1' 1, |" in result.jianpu_text


def test_duration_two_beats_outputs_extension_line(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(path, [_note(60, quantized_beat_duration=2.0)])

    result = build_numbered_notation(path)

    assert "| 1 - |" in result.jianpu_text


def test_bpm_header_uses_integer_value(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(path, [_note(60)])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bpm"] = 109.956
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_numbered_notation(path)

    assert result.bpm == 110
    assert "BPM: 110\n" in result.jianpu_text


def test_notes_are_grouped_by_bar_index(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(
        path,
        [
            _note(60, note_id="note-1", bar_index=0),
            _note(62, note_id="note-2", bar_index=1),
        ],
    )

    result = build_numbered_notation(path)

    assert [bar.bar_index for bar in result.bars] == [0, 1]
    assert "| 1 | 2 |" in result.jianpu_text


def test_missing_key_mode_uses_c_major_with_warning(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(path, [_note(60)], key=None, mode=None)

    result = build_numbered_notation(path)

    assert result.key == "C"
    assert result.mode == "major"
    assert "missing_key_mode_used_c_major" in result.warnings


def test_missing_duration_does_not_crash(tmp_path):
    path = tmp_path / "analysis" / "rhythm" / "notes_draft.json"
    _write_notes_draft(path, [_note(60, quantized_beat_duration=None)])

    result = build_numbered_notation(path)

    assert result.notes[0].numbered_notation == "1"
    assert "missing_duration" in result.notes[0].warnings
    assert "| 1 |" in result.jianpu_text


def test_numbered_notation_json_and_jianpu_text_are_written(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job")
    _write_notes_draft(artifacts.rhythm_notes_draft_json, [_note(60)])
    result = build_numbered_notation(artifacts.rhythm_notes_draft_json)

    write_numbered_notation_json(result, artifacts.rhythm_numbered_notation_json)
    write_jianpu_draft_txt(result, artifacts.rhythm_jianpu_draft_txt)

    payload = json.loads(artifacts.rhythm_numbered_notation_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "numbered_notation.v1"
    assert payload["source"] == "notes_draft"
    assert payload["notes"][0]["numbered_notation"] == "1"
    assert "Key: C" in artifacts.rhythm_jianpu_draft_txt.read_text(encoding="utf-8")


def test_builder_does_not_create_or_overwrite_melody_artifacts(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job")
    _write_notes_draft(artifacts.rhythm_notes_draft_json, [_note(60)])
    artifacts.analysis_dir.mkdir(parents=True, exist_ok=True)
    artifacts.melody_json.write_text("keep-json", encoding="utf-8")
    artifacts.melody_midi.write_bytes(b"keep-midi")

    result = build_numbered_notation(artifacts.rhythm_notes_draft_json)
    write_numbered_notation_json(result, artifacts.rhythm_numbered_notation_json)
    write_jianpu_draft_txt(result, artifacts.rhythm_jianpu_draft_txt)

    assert artifacts.melody_json.read_text(encoding="utf-8") == "keep-json"
    assert artifacts.melody_midi.read_bytes() == b"keep-midi"
