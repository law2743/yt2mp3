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


def _write_onsets(path: Path, times: list[float], *, confidence: float = 0.8) -> None:
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
                    "raw_score": confidence,
                    "backtracked_time_sec": "",
                }
            )


def _frames_for_durations(parts: list[tuple[float, float]], *, step: float = 0.02) -> list[float]:
    frames: list[float] = []
    for midi, duration_sec in parts:
        frames.extend([midi] * max(1, round(duration_sec / step)))
    return frames


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


def test_strong_pitch_jump_with_vocal_onset_records_boundary_decision(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [60] * 4 + [64] * 4)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.08], confidence=0.9)

    result = build_note_draft(pitch, beat_grid, onsets)

    assert [note.midi_note for note in result.notes] == [60, 64]
    assert result.notes[1].boundary_source == "vocal_onset"
    assert "pitch_jump" in result.notes[1].boundary_reasons
    assert "vocal_onset" in result.notes[1].boundary_reasons
    assert result.notes[1].boundary_confidence is not None
    assert result.notes[1].boundary_confidence >= 0.9
    assert result.diagnostics
    assert result.diagnostics.note_stats["strong_split_count"] >= 1


def test_short_pitch_spike_without_onset_is_removed(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [60] * 4 + [73] + [60] * 4)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [60]
    assert "absorbed_octave_spike" in result.notes[0].warnings
    assert "removed_short_spike" not in result.notes[0].warnings
    assert result.diagnostics
    assert result.diagnostics.note_stats["removed_short_spike_count"] == 1
    assert result.diagnostics.note_stats["absorbed_octave_spike_count"] == 1


def test_protected_short_note_is_not_absorbed_into_following_pitch(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(59, 0.16), (62, 0.50)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [59, 62]
    assert "protected_short_note" in result.notes[0].warnings
    assert result.diagnostics
    assert result.diagnostics.note_stats["protected_short_note_count"] >= 1
    assert result.diagnostics.note_stats["overmerge_guard_count"] >= 1


def test_protected_short_note_is_limited_to_short_durations(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(59, 0.32), (62, 0.50)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [59, 62]
    assert all("protected_short_note" not in note.warnings for note in result.notes)
    assert result.diagnostics
    assert result.diagnostics.note_stats["protected_short_note_count"] == 0


def test_extremely_short_octave_spike_is_not_output_as_note(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(60, 0.40), (73, 0.03), (60, 0.40)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [60]
    assert result.diagnostics
    stats = result.diagnostics.note_stats
    assert stats["removed_octave_spike_count"] + stats["absorbed_octave_spike_count"] > 0


def test_stable_different_short_pitch_is_not_blindly_merged(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(60, 0.14), (64, 0.40)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [60, 64]
    assert "protected_short_note" in result.notes[0].warnings


def test_same_pitch_fragments_still_merge_across_short_spike(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(60, 0.20), (62, 0.02), (60, 0.30)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [60]
    assert any(
        warning in result.notes[0].warnings
        for warning in ("absorbed_short_spike", "suppressed_short_ornament", "merged_same_pitch_fragment")
    )


def test_final_note_warnings_do_not_mix_protected_and_removed_semantics(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, _frames_for_durations([(59, 0.16), (62, 0.50)]))
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    for note in result.notes:
        warnings = set(note.warnings)
        assert not {"short_ornament_candidate", "removed_short_spike"} <= warnings
        assert not {"protected_short_note", "removed_below_min_subdivision"} <= warnings
        assert not {"protected_short_note", "removed_octave_spike"} <= warnings


def test_multiple_tiny_spikes_do_not_rebound_as_short_notes(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(
        pitch,
        _frames_for_durations(
            [
                (60, 0.20),
                (73, 0.02),
                (60, 0.20),
                (76, 0.04),
                (60, 0.20),
                (62, 0.03),
                (60, 0.20),
            ]
        ),
    )
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert all(note.duration_sec >= 0.06 for note in result.notes)
    assert len([note for note in result.notes if note.duration_sec < 0.06]) == 0


def test_clear_same_pitch_vocal_onset_can_create_boundary(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [60] * 8)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.08], confidence=0.95)

    result = build_note_draft(pitch, beat_grid, onsets)

    assert [note.midi_note for note in result.notes] == [60, 60]
    assert result.notes[1].boundary_source == "vocal_onset"
    assert result.notes[1].boundary_reasons == ["vocal_onset_same_pitch"]
    assert result.diagnostics
    assert result.diagnostics.note_stats["same_pitch_onset_boundary_count"] == 1


def test_tail_drift_is_suppressed_instead_of_split(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    _write_pitch_csv(pitch, [60.0, 60.85, 60.15, 59.35, 60.0, 60.75])
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert len(result.notes) == 1
    assert "vibrato_or_tail_drift_suppressed" in result.notes[0].warnings
    assert result.diagnostics
    assert result.diagnostics.note_stats["suppressed_split_count"] >= 1


def test_large_pitch_range_is_not_hidden_by_vibrato_suppression(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    midis = _frames_for_durations([(59, 0.20), (62, 0.50)])
    _write_pitch_csv(pitch, midis)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [59, 62]
    assert not (
        len(result.notes) == 1
        and result.notes[0].midi_note == 62
        and "vibrato_or_tail_drift_suppressed" in result.notes[0].warnings
    )


def test_unstable_segment_is_split_by_internal_pitch_plateaus(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    midis = [60] * 10 + [60.6, 61.2, 61.8, 62.4, 63.0, 63.4] + [64] * 20
    _write_pitch_csv(pitch, midis)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert [note.midi_note for note in result.notes] == [60, 64]
    assert not (len(result.notes) == 1 and result.notes[0].midi_note == 64)
    assert any(
        "intra_segment_pitch_plateau" in note.boundary_reasons
        for note in result.notes
    )


def test_true_vibrato_stays_as_one_segment(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    midis = [60.0, 60.35, 59.7, 60.25, 59.65, 60.15] * 7
    _write_pitch_csv(pitch, midis)
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert len(result.notes) == 1
    assert result.notes[0].midi_note == 60


def test_short_ornament_between_same_pitch_neighbors_is_suppressed(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [60] * 4 + [62] + [60] * 4)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.08], confidence=0.9)

    result = build_note_draft(pitch, beat_grid, onsets)

    assert [note.midi_note for note in result.notes] == [60]
    assert "suppressed_short_ornament" in result.notes[0].warnings
    assert all("short_ornament_candidate" not in note.warnings for note in result.notes)
    assert result.diagnostics
    assert result.diagnostics.note_stats["suppressed_short_ornament_count"] == 1


def test_short_ornament_with_onset_can_be_kept_with_warning(tmp_path):
    pitch = tmp_path / "fusion.csv"
    beat_grid = tmp_path / "beat_grid.json"
    onsets = tmp_path / "vocal_onsets.csv"
    _write_pitch_csv(pitch, [60] * 4 + [62] * 5 + [64] * 4)
    _write_beat_grid(beat_grid)
    _write_onsets(onsets, [0.08], confidence=0.9)

    result = build_note_draft(pitch, beat_grid, onsets)

    assert [note.midi_note for note in result.notes] == [60, 62, 64]
    assert "short_ornament_candidate" in result.notes[1].warnings
    assert "kept_short_ornament" in result.notes[1].warnings
    assert "suppressed_short_ornament" not in result.notes[1].warnings
    assert result.diagnostics
    assert result.diagnostics.note_stats["short_ornament_candidate_count"] == 1
    assert result.diagnostics.note_stats["kept_short_ornament_count"] == 1


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


def test_postprocessed_midi_column_takes_priority_over_f0(tmp_path):
    pitch = tmp_path / "postprocessed.csv"
    beat_grid = tmp_path / "beat_grid.json"
    pitch.write_text(
        "time_sec,hybrid_postprocessed_midi,hybrid_postprocessed_f0_hz,voiced\n"
        + "\n".join(f"{index * 0.02:.2f},69,523.251131,1" for index in range(12))
        + "\n",
        encoding="utf-8",
    )
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert result.pitch_source == "hybrid_postprocessed"
    assert result.notes[0].midi_note == 69


def test_postprocessed_f0_column_is_used_when_midi_is_missing(tmp_path):
    pitch = tmp_path / "postprocessed.csv"
    beat_grid = tmp_path / "beat_grid.json"
    pitch.write_text(
        "time_sec,hybrid_postprocessed_midi,hybrid_postprocessed_f0_hz,voiced\n"
        + "\n".join(f"{index * 0.02:.2f},,523.251131,1" for index in range(12))
        + "\n",
        encoding="utf-8",
    )
    _write_beat_grid(beat_grid)

    result = build_note_draft(pitch, beat_grid)

    assert result.pitch_source == "hybrid_postprocessed"
    assert result.notes[0].midi_note == 72


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
    assert "boundary_reasons" in rows[0]
    assert "boundary_confidence" in rows[0]
    assert rows[0]["segment_frame_count"] == "12"
    assert "pitch_stability_cents" in rows[0]


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
