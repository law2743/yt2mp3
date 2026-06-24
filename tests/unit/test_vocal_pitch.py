from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import uuid

import numpy as np
import pytest

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.vocal_pitch import VocalPitchResult
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job
from app.services.model_backends.rmvpe_backend import RmvpePitchBackend
from app.services.pipelines.vocal_pitch import ensure_vocal_pitch
from app.services.youtube import canonicalize_youtube_url
from scripts.run_rmvpe_pitch import _write_output


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


def _valid_payload(threshold: float = 0.03) -> dict:
    return {
        "schema_version": "vocal_pitch.v1",
        "backend": "rmvpe_onnx",
        "fallback_used": False,
        "input_source": "vocals",
        "sample_rate": 44100,
        "duration_seconds": 1.0,
        "frame_hz": 100.0,
        "hop_seconds": 0.01,
        "voiced_confidence_threshold": threshold,
        "points": [
            {
                "time": 0.0,
                "frequency_hz": 440.0,
                "midi": 69.0,
                "confidence": 0.5,
                "voiced": True,
            }
        ],
        "metadata": {
            "model": "rmvpe-onnx",
            "device": "cuda",
            "confidence_source": "rmvpe_onnx",
            "activation_shape": [2, 360],
            "created_at": "2026-06-24T00:00:00+00:00",
        },
    }


def test_rmvpe_output_schema_uses_confidence_threshold(tmp_path):
    output = tmp_path / "vocal_pitch.json"
    _write_output(
        output,
        sample_rate=44100,
        duration_seconds=1.0,
        threshold=0.03,
        time=np.asarray([0.0, 0.01]),
        frequency=np.asarray([440.0, 220.0]),
        confidence=np.asarray([0.02, 0.03]),
        activation=np.zeros((2, 360)),
    )

    result = VocalPitchResult.model_validate_json(output.read_text(encoding="utf-8"))

    assert result.backend == "rmvpe_onnx"
    assert result.fallback_used is False
    assert result.voiced_confidence_threshold == 0.03
    assert result.points[0].frequency_hz == 440.0
    assert result.points[0].midi is not None
    assert result.points[0].voiced is False
    assert result.points[1].voiced is True
    assert result.metadata.activation_shape == [2, 360]


@pytest.mark.asyncio
async def test_backend_success_writes_valid_vocal_pitch_json(tmp_path, monkeypatch):
    gpu_python = tmp_path / "python"
    gpu_python.write_text("#!/bin/sh\n", encoding="utf-8")
    gpu_python.chmod(0o755)
    settings = Settings(app_env="test", work_root=tmp_path, rmvpe_python=gpu_python)
    backend = RmvpePitchBackend(settings)
    input_wav = tmp_path / "vocals.wav"
    output_json = tmp_path / "analysis/pitch/vocal_pitch.json"
    input_wav.write_bytes(b"wav")

    async def fake_run(*command, timeout_seconds=None):
        del timeout_seconds
        temporary = Path(command[3])
        assert "--voiced-confidence-threshold" in command
        assert command[-1] == "0.03"
        temporary.write_text(json.dumps(_valid_payload()), encoding="utf-8")
        return 0, b"ok", b""

    monkeypatch.setattr(backend, "_run", fake_run)

    result = await backend.extract(input_wav, output_json)

    assert result.backend == "rmvpe_onnx"
    assert output_json.exists()
    assert not output_json.with_name(".vocal_pitch.json.tmp").exists()


@pytest.mark.asyncio
async def test_backend_failure_does_not_create_vocal_pitch_json(tmp_path, monkeypatch):
    gpu_python = tmp_path / "python"
    gpu_python.write_text("#!/bin/sh\n", encoding="utf-8")
    gpu_python.chmod(0o755)
    backend = RmvpePitchBackend(
        Settings(app_env="test", work_root=tmp_path, rmvpe_python=gpu_python)
    )
    input_wav = tmp_path / "vocals.wav"
    output_json = tmp_path / "analysis/pitch/vocal_pitch.json"
    input_wav.write_bytes(b"wav")

    async def fake_run(*command, timeout_seconds=None):
        del command, timeout_seconds
        return 2, b"", b"ERROR: rmvpe failed"

    monkeypatch.setattr(backend, "_run", fake_run)

    with pytest.raises(RuntimeError, match="RMVPE pitch extraction failed"):
        await backend.extract(input_wav, output_json)

    assert not output_json.exists()
    assert not output_json.with_name(".vocal_pitch.json.tmp").exists()


@pytest.mark.asyncio
async def test_ensure_vocal_pitch_skips_mix_without_creating_artifact(tmp_path):
    job = _job(tmp_path)
    result = await ensure_vocal_pitch(job, "mix", Settings(app_env="test", work_root=tmp_path))

    assert result.status == "skipped"
    assert not job.artifacts.vocal_pitch_json.exists()


@pytest.mark.asyncio
async def test_ensure_vocal_pitch_requires_vocals_for_vocals_source(tmp_path):
    job = _job(tmp_path)

    with pytest.raises(AppError) as error:
        await ensure_vocal_pitch(job, "vocals", Settings(app_env="test", work_root=tmp_path))

    assert error.value.code == "VOCALS_SOURCE_NOT_READY"
    assert not job.artifacts.vocal_pitch_json.exists()


@pytest.mark.asyncio
async def test_ensure_vocal_pitch_failure_does_not_fake_artifact(tmp_path, monkeypatch):
    job = _job(tmp_path)
    job.artifacts.stems_dir.mkdir()
    job.artifacts.vocals_wav.write_bytes(b"vocals")

    async def fake_extract(_self, _input_wav, _output_json):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(RmvpePitchBackend, "extract", fake_extract)

    with pytest.raises(AppError) as error:
        await ensure_vocal_pitch(job, "vocals", Settings(app_env="test", work_root=tmp_path))

    assert error.value.code == "PITCH_FAILED"
    assert not job.artifacts.vocal_pitch_json.exists()


def test_runtime_requirements_do_not_include_gpu_dependencies():
    requirements = open("requirements.txt", encoding="utf-8").read().lower()
    forbidden = ("torch", "onnxruntime", "rmvpe", "demucs", "cuda")
    dependency_lines = [
        line
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(name in line for line in dependency_lines for name in forbidden)
