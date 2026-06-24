from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.models import JobStatus
from app.models.stem import StemSeparationMetadata
from app.services.job_manager import JobManager
from app.services.model_backends.demucs_backend import DemucsStemSeparator
from app.services.stem_separator import StemSeparationRequest
from app.services.pipelines.stems import StemPipeline, read_stem_metadata
from app.services.youtube import canonicalize_youtube_url


async def _ready_job(tmp_path):
    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    job = await manager.create("owner", canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"))
    item = await manager.queue.get()
    assert item.operation == "analyze"
    manager.queue.task_done()
    source = job.artifacts.source_dir / "source.webm"
    source.write_bytes(b"source fixture")
    job.source_path = source
    job.status = JobStatus.READY
    return job


def test_stem_artifact_paths_and_metadata_round_trip(tmp_path):
    from app.services.artifacts import JobArtifacts

    artifacts = JobArtifacts(tmp_path / "job")
    artifacts.create_directories()
    assert artifacts.vocals_wav == tmp_path / "job/analysis/stems/vocals.wav"
    assert artifacts.accompaniment_wav == tmp_path / "job/analysis/stems/accompaniment.wav"
    metadata = StemSeparationMetadata(
        status="completed",
        backend="demucs",
        source_path="source/source.webm",
        vocals_path="analysis/stems/vocals.wav",
        accompaniment_path="analysis/stems/accompaniment.wav",
    )
    artifacts.stems_dir.mkdir()
    artifacts.stems_metadata_json.write_text(metadata.model_dump_json())
    assert read_stem_metadata(artifacts.stems_metadata_json) == metadata
    assert not metadata.source_path.startswith("/")


def test_demucs_environment_is_cleaned(monkeypatch, tmp_path):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/foreign/cudnn")
    monkeypatch.setenv("PYTHONPATH", "/foreign/python")
    
    backend = DemucsStemSeparator(
        Settings(app_env="test", work_root=tmp_path, demucs_clean_env=True)
    )

    environment = backend._environment()
    assert "LD_LIBRARY_PATH" not in environment
    assert "PYTHONPATH" not in environment
    assert os.environ["LD_LIBRARY_PATH"] == "/foreign/cudnn"


@pytest.mark.asyncio
async def test_demucs_backend_normalizes_raw_output(tmp_path, monkeypatch):
    settings = Settings(
        app_env="test",
        work_root=tmp_path,
        stem_separation_device="cuda",
    )
    backend = DemucsStemSeparator(settings)
    job_root = tmp_path / "job"
    source = job_root / "source/source.webm"
    stems = job_root / "analysis/stems"
    source.parent.mkdir(parents=True)
    stems.mkdir(parents=True)
    source.write_bytes(b"source")

    async def fake_probe():
        return "cuda", None

    async def fake_run(*command, timeout_seconds=None):
        del timeout_seconds
        output = command[command.index("-o") + 1]
        raw = Path(output) / settings.demucs_model / "source"
        raw.mkdir(parents=True)
        (raw / "vocals.wav").write_bytes(b"v" * 100)
        (raw / "no_vocals.wav").write_bytes(b"a" * 100)
        return 0, b"ok", b""

    monkeypatch.setattr(backend, "probe_device", fake_probe)
    monkeypatch.setattr(backend, "_run", fake_run)
    request = StemSeparationRequest(
        job_id="job",
        job_root=job_root,
        source_audio=source,
        stems_dir=stems,
        vocals_output=stems / "vocals.wav",
        accompaniment_output=stems / "accompaniment.wav",
        metadata_output=stems / "metadata.json",
    )
    result = await backend.separate(request)
    assert result.status == "completed"
    assert result.vocals_path == "analysis/stems/vocals.wav"
    assert request.vocals_output.read_bytes() == b"v" * 100
    assert request.accompaniment_output.read_bytes() == b"a" * 100


@pytest.mark.asyncio
async def test_none_fallback_creates_only_metadata(tmp_path):
    job = await _ready_job(tmp_path)
    settings = Settings(
        app_env="test",
        work_root=tmp_path,
        stem_separation_enabled=True,
        stem_separation_backend="none",
    )
    metadata = await StemPipeline(settings).run(job)
    assert metadata.status == "fallback"
    assert metadata.backend == "none"
    assert job.artifacts.stems_metadata_json.exists()
    assert not job.artifacts.vocals_wav.exists()
    assert not job.artifacts.accompaniment_wav.exists()


@pytest.mark.asyncio
async def test_fake_demucs_output_is_normalized_and_cached(tmp_path, monkeypatch):
    job = await _ready_job(tmp_path)
    settings = Settings(
        app_env="test",
        work_root=tmp_path,
        stem_separation_enabled=True,
        stem_separation_backend="demucs",
    )
    calls = 0

    async def fake_separate(_self, request):
        nonlocal calls
        calls += 1
        request.vocals_output.write_bytes(b"R" * 100)
        request.accompaniment_output.write_bytes(b"L" * 100)
        return StemSeparationMetadata(
            status="completed",
            backend="demucs",
            model="fake",
            device="cuda",
            source_path="source/source.webm",
            vocals_path="analysis/stems/vocals.wav",
            accompaniment_path="analysis/stems/accompaniment.wav",
        )

    monkeypatch.setattr(DemucsStemSeparator, "separate", fake_separate)
    first = await StemPipeline(settings).run(job)
    second = await StemPipeline(settings).run(job)
    assert first.status == "completed"
    assert second.cached is True
    assert calls == 1
