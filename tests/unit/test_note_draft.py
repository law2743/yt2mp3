from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models.rhythm import BeatGridResult
from app.services.rhythm.note_draft import (
    build_note_draft,
    write_note_draft_csv,
    write_note_draft_json,
)


def _write_pitch_csv(
    path: Path,
    midis: list[float],
    *,
    step: float = 0.02,
    column: str = "hybrid_postprocessed",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["time_sec", column, "confidence", "voiced"],
        )
        writer.writeheader()
        for index, midi in enumerate(midis):
            voiced = midi > 0
            writer.writerow(
                {
                    "time_sec": round(index * step, 6),
                    column: "" if not voiced else midi,
                    "confidence": 0.9 if voiced else 0.0,
                    "voiced": 1 if voiced else 0,
                }
            )


def _write_pitch_json(path: Path, midis: list[float], *, step: float = 0.02) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [
        {
            "time_sec": round(index * step, 6),
            "midi": midi,
            "f0_hz": 440.0 * (2.0 ** ((midi - 69.0) / 12.0)) if midi > 0 else 0,
            "confidence": 0.9 if midi > 0 else 0,
            "voiced": midi > 0,
        }
        for index, midi in enumerate(midis)
    ]
    path.write_text(
        json.dumps({"frames": frames, "frame_period_ms": step * 1000}),
        encoding="utf-8",
    )


def _write_beat_grid(path: Path, *, meter: str = "4/4") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = BeatGridResult(
        algorithm_version="test",
        duration_seconds=4.0,
        bpm=120.0,
        meter=meter,
        meter_used=meter,
        beats_per_bar=4 if meter == "4/4" else None,
        beat_times_sec=[0.0, 0.5, 1.0, 1.5, 2.0],
        beats=[],
    )
    path.write_text(result.model_dump_json(), encoding="utf-8")


def _write_onsets(path: Path, times: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["onset_id", "time_sec", "raw_score", "backtracked_time_sec"],
        )
        writer.writeheader()
        for index, time_sec in enumerate(times):
            writer.writerow(
                {
                    "onset_id": f"onset-{index + 1:04d}",
                    "time_sec": time_sec,
                    "raw_score": 0.8,
                    "backtracked_time_sec": "",
                }
            )


def test_stable_pitch_produces_one_note_draft(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 12)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert len(result.notes) == 1
    assert result.notes[0].midi_note == 69
    assert result.notes[0].note_name == "A4"
    assert result.notes[0].pitch_source == "hybrid_postprocessed"


def test_unvoiced_gap_splits_notes(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 6 + [0] * 5 + [69] * 6)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert len(result.notes) == 2


def test_pitch_jump_over_threshold_splits_notes(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 6 + [72] * 6)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [69, 72]
    assert result.notes[1].boundary_source == "pitch_change"


def test_vocal_onset_does_not_over_split_stable_pitch(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [69] * 14)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.12])

    result = build_note_draft(pitch, beat_grid, onsets)

    assert len(result.notes) == 1
    assert result.notes[0].onset_confidence == 0.8


def test_vocal_onset_with_pitch_change_can_mark_boundary_source(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [69] * 6 + [72] * 6)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.12])

    result = build_note_draft(pitch, beat_grid, onsets)

    assert len(result.notes) == 2
    assert result.notes[1].boundary_source == "vocal_onset"


def test_too_short_note_is_discarded(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 6 + [72] * 2 + [76] * 6)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid, min_note_duration_sec=0.06)

    assert [note.midi_note for note in result.notes] == [69, 76]


def test_beat_grid_fills_raw_beat_fields(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 12)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert result.notes[0].raw_beat_start == 0.0
    assert result.notes[0].raw_beat_duration is not None
    assert result.notes[0].bar_index == 0


def test_four_four_meter_fills_quantized_fields(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [69] * 13)
    _write_beat_grid(beat_grid, meter="4/4")

    result = build_note_draft(pitch, beat_grid)

    assert result.meter_used == "4/4"
    assert result.notes[0].quantized_beat_start == 0.0
    assert result.notes[0].quantized_beat_duration is not None
    assert result.notes[0].quantization_confidence is not None


def test_insufficient_beat_grid_keeps_notes_without_beat_fields(tmp_path):
    pitch = tmp_path / "fusion.json"
    beat_grid = tmp_path / "missing.json"
    _write_pitch_json(pitch, [69] * 10)

    result = build_note_draft(pitch, beat_grid)

    assert len(result.notes) == 1
    assert result.notes[0].raw_beat_start is None
    assert "missing_or_insufficient_beat_grid" in result.warnings


def test_missing_pitch_column_returns_empty_notes_with_warning(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    pitch.write_text("time_sec,confidence\n0.0,0.9\n", encoding="utf-8")
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert result.notes == []
    assert "missing_pitch_column" in result.warnings


def test_write_note_draft_csv_outputs_expected_columns(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    output = tmp_path / "notes_draft.csv"
    _write_pitch_csv(pitch, [69] * 12)
    _write_beat_grid(beat_grid)
    result = build_note_draft(pitch, beat_grid)

    write_note_draft_csv(result, output)

    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["note_id"] == "note-0001"
    assert rows[0]["note_name"] == "A4"
    assert rows[0]["boundary_source"] == "hybrid"


def test_write_note_draft_json_outputs_result_payload(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    output = tmp_path / "notes_draft.json"
    _write_pitch_csv(pitch, [69] * 12)
    _write_beat_grid(beat_grid)
    result = build_note_draft(pitch, beat_grid)

    write_note_draft_json(result, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "rhythm.notes_draft.v1"
    assert payload["notes"][0]["note_name"] == "A4"
