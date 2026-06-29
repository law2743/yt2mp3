from __future__ import annotations

import json
from pathlib import Path

from app.models.rhythm import NoteDraft, NoteDraftResult
from app.services.artifacts import JobArtifacts
import app.services.rhythm.notation_generation as notation_generation
from app.services.rhythm.notation_generation import try_generate_notation_artifacts


def _artifacts(tmp_path: Path) -> JobArtifacts:
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.melody_json.write_text("keep-json", encoding="utf-8")
    artifacts.melody_midi.write_bytes(b"keep-midi")
    return artifacts


def _write_notes_draft(path: Path) -> None:
    result = NoteDraftResult(
        algorithm_version="fixture",
        bpm=120,
        meter_used="4/4",
        notes=[
            NoteDraft(
                note_id="n1",
                start_sec=0,
                end_sec=0.5,
                duration_sec=0.5,
                midi_note=60,
                note_name="C4",
                bar_index=0,
                raw_beat_start=0,
                raw_beat_duration=1,
                quantized_beat_start=0,
                quantized_beat_duration=1,
            )
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def test_try_generate_notation_artifacts_writes_numbered_and_jianpu(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)

    def fake_run(job_dir, *, meter_hint="auto", force=False):
        assert job_dir == artifacts.root
        assert meter_hint == "auto"
        assert force is False
        _write_notes_draft(artifacts.rhythm_notes_draft_json)

    monkeypatch.setattr(notation_generation, "run_rhythm_pipeline", fake_run)

    assert try_generate_notation_artifacts(artifacts.root, key="C", mode="major") is True

    payload = json.loads(artifacts.rhythm_numbered_notation_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "numbered_notation.v1"
    assert payload["notes"][0]["numbered_notation"] == "1"
    assert "Key: C" in artifacts.rhythm_jianpu_draft_txt.read_text(encoding="utf-8")
    assert artifacts.melody_json.read_text(encoding="utf-8") == "keep-json"
    assert artifacts.melody_midi.read_bytes() == b"keep-midi"


def test_try_generate_notation_artifacts_returns_false_when_rhythm_pipeline_fails(
    tmp_path,
    monkeypatch,
):
    artifacts = _artifacts(tmp_path)

    def fake_run(_job_dir, *, meter_hint="auto", force=False):
        raise RuntimeError("rhythm failed")

    monkeypatch.setattr(notation_generation, "run_rhythm_pipeline", fake_run)

    assert try_generate_notation_artifacts(artifacts.root, key="C", mode="major") is False
    assert not artifacts.rhythm_numbered_notation_json.exists()
    assert not artifacts.rhythm_jianpu_draft_txt.exists()
    assert artifacts.melody_json.read_text(encoding="utf-8") == "keep-json"
    assert artifacts.melody_midi.read_bytes() == b"keep-midi"


def test_try_generate_notation_artifacts_returns_false_when_numbered_notation_fails(
    tmp_path,
    monkeypatch,
):
    artifacts = _artifacts(tmp_path)

    def fake_run(_job_dir, *, meter_hint="auto", force=False):
        _write_notes_draft(artifacts.rhythm_notes_draft_json)

    def fake_build(_notes_draft_path, *, key=None, mode=None):
        raise RuntimeError("notation failed")

    monkeypatch.setattr(notation_generation, "run_rhythm_pipeline", fake_run)
    monkeypatch.setattr(notation_generation, "build_numbered_notation", fake_build)

    assert try_generate_notation_artifacts(artifacts.root, key="C", mode="major") is False
    assert not artifacts.rhythm_numbered_notation_json.exists()
    assert not artifacts.rhythm_jianpu_draft_txt.exists()
    assert artifacts.melody_json.read_text(encoding="utf-8") == "keep-json"
    assert artifacts.melody_midi.read_bytes() == b"keep-midi"
