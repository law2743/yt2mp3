from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.api.jobs import download_notation_artifact
from app.config import Settings
from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyAnalysisResult, MelodyStatus, MelodySummary
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job, JobManager
from app.services.youtube import canonicalize_youtube_url


OWNER_ID = "00000000-0000-0000-0000-000000000000"


def _manager(tmp_path: Path) -> JobManager:
    return JobManager(Settings(app_env="test", app_password=None, work_root=tmp_path))


def _insert_ready_job(manager: JobManager) -> Job:
    job_id = str(uuid.uuid4())
    artifacts = JobArtifacts(manager.settings.work_root / job_id)
    artifacts.create_directories()
    artifacts.analysis_audio.write_bytes(b"fixture")
    now = datetime.now(UTC)
    job = Job(
        job_id=job_id,
        owner_id=OWNER_ID,
        youtube_url=canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
        artifacts=artifacts,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        status=JobStatus.READY,
        stage="awaiting_selection",
        progress=100,
        analysis=KeyAnalysisResult(
            root_index=0,
            root_name="C",
            mode="major",
            display_name="C Major",
            confidence=0.8,
            candidates=[KeyCandidate(key="C Major", score=1)],
            algorithm_version="fixture",
        ),
    )
    manager.jobs[job_id] = job
    return job


def _complete_melody(job: Job) -> None:
    result = MelodyAnalysisResult(
        job_id=job.job_id,
        key="C Major",
        mode="major",
        meter_hint="none",
        notes=[],
        summary=MelodySummary(note_count=0, voiced_ratio=0, average_confidence=0),
    )
    job.artifacts.melody_json.write_text("keep-json", encoding="utf-8")
    job.artifacts.melody_midi.write_bytes(b"keep-midi")
    job.melody.status = MelodyStatus.COMPLETED
    job.melody.stage = "completed"
    job.melody.progress = 100
    job.melody.result = result


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _request(manager: JobManager):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_manager=manager)))


def test_notation_artifacts_absent_keeps_job_and_melody_responses_compatible(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _complete_melody(job)

    status = manager.public(job).model_dump(mode="json")
    melody = manager.melody_public(job)

    assert status["notation_artifacts"] == {
        "available": False,
        "numbered_notation_json_url": None,
        "jianpu_draft_txt_url": None,
        "notes_draft_json_url": None,
        "notes_draft_csv_url": None,
        "rhythm_diagnostics_json_url": None,
        "warnings": ["notation_artifacts_not_found"],
    }
    assert melody["result"]["downloads"] == {
        "json_url": f"/api/jobs/{job.job_id}/melody/download/json",
        "midi_url": f"/api/jobs/{job.job_id}/melody/download/midi",
    }
    assert melody["notation_artifacts"]["available"] is False
    assert melody["artifact_status"]["vocals_wav"] is False
    assert melody["artifact_status"]["jianpu_draft_txt"] is False
    assert not job.artifacts.rhythm_numbered_notation_json.exists()
    assert not job.artifacts.rhythm_jianpu_draft_txt.exists()
    assert job.artifacts.melody_json.read_text(encoding="utf-8") == "keep-json"
    assert job.artifacts.melody_midi.read_bytes() == b"keep-midi"


def test_melody_response_exposes_artifact_status_for_step_two_progress(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _complete_melody(job)
    _write_artifact(job.artifacts.vocals_wav, "vocals")
    _write_artifact(job.artifacts.melody_fusion_input_csv("rmvpe"), "time_sec,midi\n")
    _write_artifact(job.artifacts.melody_fusion_input_csv("torchcrepe"), "time_sec,midi\n")
    _write_artifact(job.artifacts.melody_fusion_input_csv("fcpe"), "time_sec,midi\n")
    _write_artifact(job.artifacts.melody_fusion_input_csv("pesto"), "time_sec,midi\n")
    _write_artifact(job.artifacts.melody_fusion_csv, "time_sec,midi\n")
    _write_artifact(job.artifacts.melody_fusion_json, "{}")
    _write_artifact(job.artifacts.rhythm_beat_grid_json, "{}")
    _write_artifact(job.artifacts.rhythm_vocal_onsets_csv, "onset_id,time_sec\n")
    _write_artifact(job.artifacts.rhythm_notes_draft_json, "{}")
    _write_artifact(job.artifacts.rhythm_numbered_notation_json, "{}")
    _write_artifact(job.artifacts.rhythm_jianpu_draft_txt, "Key: C\n")

    status = manager.melody_public(job)["artifact_status"]

    assert status == {
        "vocals_wav": True,
        "rmvpe_csv": True,
        "torchcrepe_csv": True,
        "fcpe_csv": True,
        "pesto_csv": True,
        "fusion_csv": True,
        "fusion_json": True,
        "melody_json": True,
        "beat_grid_json": True,
        "vocal_onsets_csv": True,
        "notes_draft_json": True,
        "numbered_notation_json": True,
        "jianpu_draft_txt": True,
    }


def test_numbered_notation_json_makes_notation_available(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _write_artifact(job.artifacts.rhythm_numbered_notation_json, "{}")

    payload = manager.public(job).model_dump(mode="json")["notation_artifacts"]

    assert payload["available"] is True
    assert payload["numbered_notation_json_url"] == (
        f"/api/jobs/{job.job_id}/notation/download/numbered-notation-json"
    )
    assert payload["warnings"] == []


def test_jianpu_draft_txt_makes_notation_available(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _write_artifact(job.artifacts.rhythm_jianpu_draft_txt, "Key: C\n")

    payload = manager.melody_public(job)["notation_artifacts"]

    assert payload["available"] is True
    assert payload["jianpu_draft_txt_url"] == (
        f"/api/jobs/{job.job_id}/notation/download/jianpu-draft-txt"
    )


def test_note_draft_and_diagnostics_urls_are_exposed_when_present(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _write_artifact(job.artifacts.rhythm_notes_draft_json, "{}")
    _write_artifact(job.artifacts.rhythm_notes_draft_csv, "note_id\n")
    _write_artifact(job.artifacts.rhythm_diagnostics_json, "{}")

    payload = manager.public(job).model_dump(mode="json")["notation_artifacts"]

    assert payload["available"] is False
    assert payload["notes_draft_json_url"] == (
        f"/api/jobs/{job.job_id}/notation/download/notes-draft-json"
    )
    assert payload["notes_draft_csv_url"] == (
        f"/api/jobs/{job.job_id}/notation/download/notes-draft-csv"
    )
    assert payload["rhythm_diagnostics_json_url"] == (
        f"/api/jobs/{job.job_id}/notation/download/rhythm-diagnostics-json"
    )


def test_notation_download_route_returns_file_response_for_existing_artifact(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)
    _write_artifact(job.artifacts.rhythm_jianpu_draft_txt, "Key: C\n")

    response = asyncio.run(
        download_notation_artifact(
            job.job_id,
            "jianpu-draft-txt",
            _request(manager),
            owner_id=OWNER_ID,
        )
    )

    assert response.path == job.artifacts.rhythm_jianpu_draft_txt
    assert response.media_type == "text/plain"


def test_missing_notation_download_returns_404_without_creating_artifacts(tmp_path):
    manager = _manager(tmp_path)
    job = _insert_ready_job(manager)

    with pytest.raises(AppError) as error:
        asyncio.run(
            download_notation_artifact(
                job.job_id,
                "numbered-notation-json",
                _request(manager),
                owner_id=OWNER_ID,
            )
        )

    assert error.value.status_code == 404
    assert error.value.code == "NOTATION_ARTIFACT_NOT_FOUND"
    assert not job.artifacts.rhythm_numbered_notation_json.exists()
    assert not job.artifacts.rhythm_jianpu_draft_txt.exists()
    assert not job.artifacts.melody_json.exists()
    assert not job.artifacts.melody_midi.exists()
