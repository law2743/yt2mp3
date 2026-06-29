from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models.rhythm import (
    BeatEvent,
    BeatGridResult,
    NoteDraft,
    NoteDraftResult,
    RhythmDiagnostics,
    VocalOnsetEvent,
)
from app.services.artifacts import JobArtifacts
from app.services.rhythm.note_draft import write_note_draft_csv, write_note_draft_json
from app.services.rhythm.vocal_onset import write_vocal_onsets_csv
from scripts.export_rhythm_debug import export_rhythm_debug


def _artifacts(tmp_path: Path) -> JobArtifacts:
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.rhythm_dir.mkdir()
    return artifacts


def _write_all_rhythm_artifacts(artifacts: JobArtifacts) -> None:
    beat_grid = BeatGridResult(
        algorithm_version="test",
        source_audio_path=str(artifacts.accompaniment_wav),
        duration_seconds=2.0,
        bpm=120.0,
        meter="4/4",
        meter_used="4/4",
        beats_per_bar=4,
        beat_times_sec=[0.0, 0.5, 1.0, 1.5],
        bar_starts_sec=[0.0],
        beats=[
            BeatEvent(
                beat_index=index,
                time_sec=time_sec,
                bar_index=0,
                beat_in_bar=index + 1,
                tempo_bpm=120.0,
                confidence=0.9,
            )
            for index, time_sec in enumerate([0.0, 0.5, 1.0, 1.5])
        ],
    )
    artifacts.rhythm_beat_grid_json.write_text(
        beat_grid.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    write_vocal_onsets_csv(
        [
            VocalOnsetEvent(
                onset_id="onset-0001",
                time_sec=0.1,
                confidence=0.8,
                raw_score=0.8,
                backtracked_time_sec=0.08,
            ),
            VocalOnsetEvent(
                onset_id="onset-0002",
                time_sec=0.7,
                confidence=0.7,
                raw_score=0.7,
            ),
        ],
        artifacts.rhythm_vocal_onsets_csv,
    )

    result = NoteDraftResult(
        algorithm_version="test",
        pitch_source="hybrid_postprocessed",
        bpm=120.0,
        meter_used="4/4",
        notes=[
            NoteDraft(
                note_id="note-0001",
                start_sec=0.0,
                end_sec=0.5,
                duration_sec=0.5,
                midi_note=69,
                note_name="A4",
                frequency_hz=440.0,
                raw_beat_start=0.0,
                raw_beat_duration=1.0,
                quantized_beat_start=0.0,
                quantized_beat_duration=1.0,
                bar_index=0,
                pitch_confidence=0.9,
                onset_confidence=0.8,
                quantization_confidence=0.95,
                boundary_source="hybrid",
            )
        ],
        diagnostics=RhythmDiagnostics(
            algorithm_version="test",
            pitch_source="hybrid_postprocessed",
            note_stats={"used_fallback_audio": False},
            warnings=["diagnostic-warning"],
        ),
        warnings=["note-warning"],
    )
    write_note_draft_json(result, artifacts.rhythm_notes_draft_json)
    write_note_draft_csv(result, artifacts.rhythm_notes_draft_csv)
    artifacts.rhythm_diagnostics_json.write_text(
        result.diagnostics.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_export_writes_rhythm_summary_json_when_artifacts_exist(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)

    summary = export_rhythm_debug(artifacts.root, tmp_path / "debug")

    output = tmp_path / "debug" / "rhythm_summary.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert summary["note_count"] == 1
    assert payload["beat_count"] == 4
    assert payload["onset_count"] == 2
    assert payload["pitch_source"] == "hybrid_postprocessed"
    assert payload["artifact_exists"]["beat_grid"] is True


def test_export_writes_quality_report_when_artifacts_exist(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    report = (tmp_path / "debug" / "rhythm_quality_report.txt").read_text(encoding="utf-8")
    assert "Beat Grid Summary" in report
    assert "Vocal Onset Summary" in report
    assert "Note Draft Summary" in report
    assert "Diagnostics" in report
    assert "notes_draft is not final numbered notation" in report


def test_export_writes_beat_grid_preview_csv_when_artifacts_exist(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    rows = _read_csv(tmp_path / "debug" / "beat_grid_preview.csv")
    assert rows[0]["index"] == "0"
    assert rows[0]["time_sec"] == "0.0"
    assert rows[0]["beat_in_bar"] == "1"


def test_export_writes_vocal_onsets_preview_csv_when_artifacts_exist(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    rows = _read_csv(tmp_path / "debug" / "vocal_onsets_preview.csv")
    assert rows[0]["onset_id"] == "onset-0001"
    assert rows[0]["raw_score"] == "0.800000"
    assert rows[0]["source_backend"] == "librosa"


def test_export_writes_notes_draft_preview_csv_when_artifacts_exist(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    rows = _read_csv(tmp_path / "debug" / "notes_draft_preview.csv")
    assert rows[0]["note_id"] == "note-0001"
    assert rows[0]["note_name"] == "A4"
    assert rows[0]["boundary_source"] == "hybrid"


def test_export_does_not_crash_when_some_artifacts_are_missing(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)
    artifacts.rhythm_vocal_onsets_csv.unlink()
    artifacts.rhythm_diagnostics_json.unlink()

    summary = export_rhythm_debug(artifacts.root, tmp_path / "debug")

    assert summary["onset_count"] == 0
    assert (tmp_path / "debug" / "rhythm_summary.json").exists()
    assert (tmp_path / "debug" / "vocal_onsets_preview.csv").exists()


def test_export_summary_records_missing_artifacts(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)
    artifacts.rhythm_notes_draft_csv.unlink()

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    payload = json.loads((tmp_path / "debug" / "rhythm_summary.json").read_text(encoding="utf-8"))
    assert payload["artifact_exists"]["notes_draft_csv"] is False
    assert "notes_draft_csv" in payload["missing_artifacts"]


def test_export_does_not_create_or_overwrite_melody_json(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)
    artifacts.melody_json.write_text("existing melody", encoding="utf-8")

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    assert artifacts.melody_json.read_text(encoding="utf-8") == "existing melody"


def test_export_does_not_create_or_overwrite_melody_midi(tmp_path):
    artifacts = _artifacts(tmp_path)
    _write_all_rhythm_artifacts(artifacts)
    artifacts.melody_midi.write_bytes(b"existing midi")

    export_rhythm_debug(artifacts.root, tmp_path / "debug")

    assert artifacts.melody_midi.read_bytes() == b"existing midi"
