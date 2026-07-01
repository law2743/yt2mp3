from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.api.auth import authenticated_owner, issue_access_token
from app.api.jobs import download_melody_json, download_melody_midi
from app.config import Settings
from app.main import app
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyAnalysisResult, MelodyStatus, MelodySummary
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job
from app.services.youtube import canonicalize_youtube_url
from tests.unit.api_client import api_client


def _settings(tmp_path: Path, **updates) -> Settings:
    values = {"app_env": "test", "work_root": tmp_path, **updates}
    return Settings(**values)


def _insert_ready_job(manager, owner_id: str) -> Job:
    job_id = str(uuid.uuid4())
    artifacts = JobArtifacts(manager.settings.work_root / job_id)
    artifacts.create_directories()
    artifacts.analysis_audio.write_bytes(b"fixture")
    now = datetime.now(UTC)
    job = Job(
        job_id=job_id,
        owner_id=owner_id,
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
    job.artifacts.melody_json.write_text(result.model_dump_json())
    job.artifacts.melody_midi.write_bytes(b"MThd-fixture")
    job.melody.status = MelodyStatus.COMPLETED
    job.melody.stage = "completed"
    job.melody.progress = 100
    job.melody.result = result


def _request(manager):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_manager=manager)))


@pytest.mark.asyncio
async def test_melody_status_and_downloads_use_owned_job(tmp_path):
    settings = _settings(tmp_path)

    async def owner_override():
        return "00000000-0000-0000-0000-000000000000"

    app.dependency_overrides[authenticated_owner] = owner_override
    try:
        async with api_client(settings) as client:
            job = _insert_ready_job(app.state.job_manager, "00000000-0000-0000-0000-000000000000")
            response = await client.get(f"/api/jobs/{job.job_id}/melody")
            assert response.status_code == 200
            assert response.json()["status"] == "not_started"

            _complete_melody(job)
            json_download = await download_melody_json(
                job.job_id,
                _request(app.state.job_manager),
                owner_id="00000000-0000-0000-0000-000000000000",
            )
            midi_download = await download_melody_midi(
                job.job_id,
                _request(app.state.job_manager),
                owner_id="00000000-0000-0000-0000-000000000000",
            )
            assert json_download.path == job.artifacts.melody_json
            assert json_download.media_type == "application/json"
            assert midi_download.path == job.artifacts.melody_midi
            assert midi_download.media_type == "audio/midi"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_melody_auth_owner_and_meter_validation(tmp_path):
    settings = _settings(
        tmp_path,
        app_env="production",
        app_password="test-password",
        token_secret="x" * 40,
        cors_allowed_origins="https://frontend.example",
    )
    owner_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    owner_token = issue_access_token(owner_id, settings)
    other_token = issue_access_token(other_id, settings)
    async with api_client(settings) as client:
        job = _insert_ready_job(app.state.job_manager, owner_id)
        path = f"/api/jobs/{job.job_id}/melody"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={"Authorization": f"Bearer {other_token}"})
        ).status_code == 404
        invalid = await client.post(
            path,
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"meter_hint": "7/8"},
        )
        assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_disabled_melody_api_is_hidden(tmp_path):
    settings = _settings(tmp_path, enable_melody_analysis=False)

    async def owner_override():
        return "00000000-0000-0000-0000-000000000000"

    app.dependency_overrides[authenticated_owner] = owner_override
    try:
        async with api_client(settings) as client:
            job = _insert_ready_job(app.state.job_manager, "00000000-0000-0000-0000-000000000000")
            response = await client.get(f"/api/jobs/{job.job_id}/melody")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    finally:
        app.dependency_overrides.clear()
