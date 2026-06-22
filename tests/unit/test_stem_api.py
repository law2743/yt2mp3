from __future__ import annotations

from datetime import UTC, datetime, timedelta
import uuid

import httpx
import pytest

from app.api.auth import authenticated_owner
from app.config import Settings
from app.main import app
from app.models import JobStatus
from app.models.stem import StemSeparationMetadata, StemTaskStatus
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job, JobManager
from app.services.youtube import canonicalize_youtube_url


def _ready_job(manager: JobManager, owner: str) -> Job:
    job_id = str(uuid.uuid4())
    artifacts = JobArtifacts(manager.settings.work_root / job_id)
    artifacts.create_directories()
    source = artifacts.source_dir / "source.webm"
    source.write_bytes(b"source")
    now = datetime.now(UTC)
    job = Job(
        job_id=job_id,
        owner_id=owner,
        youtube_url=canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
        artifacts=artifacts,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        status=JobStatus.READY,
        source_path=source,
    )
    manager.jobs[job_id] = job
    return job


@pytest.mark.asyncio
async def test_stem_trigger_status_downloads_and_ownership(tmp_path):
    owner = str(uuid.uuid4())
    settings = Settings(
        app_env="test",
        work_root=tmp_path,
        stem_separation_enabled=True,
        stem_separation_backend="none",
    )
    manager = JobManager(settings)
    app.state.job_manager = manager

    async def owner_override():
        return owner

    app.dependency_overrides[authenticated_owner] = owner_override
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            job = _ready_job(manager, owner)
            response = await client.post(f"/api/jobs/{job.job_id}/stems", json={})
            assert response.status_code == 202
            assert response.json()["status"] == "stems_queued"

            queued = await manager.stem_queue.get()
            assert queued.operation == "stems"
            manager.stem_queue.task_done()

            job.artifacts.stems_dir.mkdir(exist_ok=True)
            job.artifacts.vocals_wav.write_bytes(b"v" * 100)
            job.artifacts.accompaniment_wav.write_bytes(b"a" * 100)
            job.stems.status = StemTaskStatus.COMPLETED
            job.stems.metadata = StemSeparationMetadata(
                status="completed",
                backend="demucs",
                source_path="source/source.webm",
                vocals_path="analysis/stems/vocals.wav",
                accompaniment_path="analysis/stems/accompaniment.wav",
            )
            completed = await client.get(f"/api/jobs/{job.job_id}/stems")
            assert completed.status_code == 200
            assert completed.json()["downloads"] == {
                "vocals_url": f"/api/jobs/{job.job_id}/stems/vocals",
                "accompaniment_url": f"/api/jobs/{job.job_id}/stems/accompaniment",
            }

            async def foreign_owner_override():
                return str(uuid.uuid4())

            app.dependency_overrides[authenticated_owner] = foreign_owner_override
            hidden = await client.get(f"/api/jobs/{job.job_id}/stems")
            assert hidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
