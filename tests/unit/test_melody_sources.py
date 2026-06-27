from __future__ import annotations

from datetime import UTC, datetime, timedelta
import uuid

import pytest

from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job
from app.services.pipelines.melody import resolve_melody_source
from app.services.youtube import canonicalize_youtube_url


def _job(tmp_path):
    artifacts = JobArtifacts(tmp_path / str(uuid.uuid4()))
    artifacts.create_directories()
    artifacts.analysis_audio.write_bytes(b"mix")
    now = datetime.now(UTC)
    return Job(
        job_id=artifacts.root.name,
        owner_id="owner",
        youtube_url=canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
        artifacts=artifacts,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        status=JobStatus.READY,
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


def test_auto_requires_vocals(tmp_path):
    job = _job(tmp_path)
    with pytest.raises(AppError) as error:
        resolve_melody_source(job, "auto")
    assert error.value.code == "VOCALS_SOURCE_NOT_READY"
    job.artifacts.stems_dir.mkdir()
    job.artifacts.vocals_wav.write_bytes(b"vocals")
    used, path = resolve_melody_source(job, "auto")
    assert (used, path) == ("vocals", job.artifacts.vocals_wav)


def test_auto_prefers_existing_vocals_over_mix(tmp_path):
    job = _job(tmp_path)
    job.artifacts.stems_dir.mkdir()
    job.artifacts.vocals_wav.write_bytes(b"vocals")

    used, path = resolve_melody_source(job, "auto")

    assert used == "vocals"
    assert path == job.artifacts.vocals_wav


def test_explicit_vocals_requires_stem(tmp_path):
    job = _job(tmp_path)
    with pytest.raises(AppError) as error:
        resolve_melody_source(job, "vocals")
    assert error.value.code == "VOCALS_SOURCE_NOT_READY"


def test_variant_paths_are_source_scoped(tmp_path):
    job = _job(tmp_path)
    with pytest.raises(ValueError):
        job.artifacts.melody_variant_json("mix")
    assert job.artifacts.melody_variant_json("vocals").name == "vocals_adaptive_fusion.json"
