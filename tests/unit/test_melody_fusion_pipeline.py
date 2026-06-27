from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import uuid

import pytest

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyAnalysisResult, MelodySummary
from app.services.artifacts import JobArtifacts
from app.services.job_manager import Job
from app.services.melody_fusion.io import write_pitch_csv
from app.services.pipelines.melody_fusion_pipeline import MelodyFusionPipeline
from app.services.youtube import canonicalize_youtube_url


def _job(tmp_path):
    artifacts = JobArtifacts(tmp_path / str(uuid.uuid4()))
    artifacts.create_directories()
    artifacts.analysis_audio.write_bytes(b"mix")
    artifacts.stems_dir.mkdir()
    artifacts.vocals_wav.write_bytes(b"vocals")
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


def _status(backend: str, path) -> dict:
    return {
        "backend": backend,
        "status": "succeeded",
        "input_path": str(path),
        "rows": 20,
        "confidence_kind": "voicing",
        "missing_confidence_rows": 0,
        "voiced_ratio": 1.0,
    }


def _write_csv(path, backend: str) -> None:
    write_pitch_csv(
        path,
        [
            {
                "time_sec": index * 0.01,
                "f0_hz": 440.0,
                "raw_f0_hz": 440.0,
                "confidence": 0.9,
                "confidence_kind": "voicing",
                "voiced": True,
                "backend": backend,
            }
            for index in range(20)
        ],
    )


@pytest.mark.asyncio
async def test_fusion_pipeline_fails_without_falling_back_when_too_few_backends(tmp_path, monkeypatch):
    job = _job(tmp_path)
    job.artifacts.melody_json.write_text("old melody", encoding="utf-8")
    pipeline = MelodyFusionPipeline(Settings(app_env="test", work_root=tmp_path))

    async def fake_prepare(_job, _source):
        return _job.artifacts.vocals_mono_16000_wav

    async def fake_extract(_job):
        rmvpe = _job.artifacts.melody_fusion_input_csv("rmvpe")
        _write_csv(rmvpe, "rmvpe")
        return {
            "rmvpe": _status("rmvpe", rmvpe),
            "torchcrepe": {"backend": "torchcrepe", "status": "failed", "failed_reason": "fixture"},
            "fcpe": {"backend": "fcpe", "status": "failed", "failed_reason": "fixture"},
            "pesto": {"backend": "pesto", "status": "failed", "failed_reason": "fixture"},
        }

    monkeypatch.setattr(pipeline, "_ensure_vocals_mono_16000", fake_prepare)
    monkeypatch.setattr(pipeline, "_extract_backend_csvs", fake_extract)

    with pytest.raises(AppError) as error:
        await pipeline.run(job, "none", "vocals")

    assert error.value.code == "MELODY_FUSION_FAILED"
    assert job.artifacts.melody_json.read_text(encoding="utf-8") == "old melody"
    diagnostics = json.loads(job.artifacts.melody_fusion_diagnostics_json.read_text())
    assert diagnostics["fusion_status"] == "failed"
    assert diagnostics["failed_reason"] == "not_enough_successful_backends"
    assert diagnostics["succeeded_backends"] == ["rmvpe"]


@pytest.mark.asyncio
async def test_fusion_pipeline_writes_compatible_melody_outputs(tmp_path, monkeypatch):
    job = _job(tmp_path)
    pipeline = MelodyFusionPipeline(Settings(app_env="test", work_root=tmp_path))

    async def fake_prepare(_job, _source):
        return _job.artifacts.vocals_mono_16000_wav

    async def fake_extract(_job):
        rmvpe = _job.artifacts.melody_fusion_input_csv("rmvpe")
        fcpe = _job.artifacts.melody_fusion_input_csv("fcpe")
        _write_csv(rmvpe, "rmvpe")
        _write_csv(fcpe, "fcpe")
        return {
            "rmvpe": _status("rmvpe", rmvpe),
            "torchcrepe": {"backend": "torchcrepe", "status": "failed", "failed_reason": "fixture"},
            "fcpe": _status("fcpe", fcpe),
            "pesto": {"backend": "pesto", "status": "failed", "failed_reason": "fixture"},
        }

    def fake_analyze(_source, _fusion_json, json_output, midi_output, **kwargs):
        result = MelodyAnalysisResult(
            job_id=kwargs["job_id"],
            algorithm_version="adaptive-melody-fusion-v1",
            key="C Major",
            mode="major",
            meter_hint="none",
            pitch_backend="adaptive_fusion",
            is_fallback=False,
            notes=[],
            summary=MelodySummary(note_count=0, voiced_ratio=1.0, average_confidence=0.0),
        )
        json_output.write_text(result.model_dump_json(), encoding="utf-8")
        midi_output.write_bytes(b"MThd-fixture")

    monkeypatch.setattr(pipeline, "_ensure_vocals_mono_16000", fake_prepare)
    monkeypatch.setattr(pipeline, "_extract_backend_csvs", fake_extract)
    monkeypatch.setattr(
        "app.services.pipelines.melody_fusion_pipeline.analyze_fusion_melody",
        fake_analyze,
    )

    result = await pipeline.run(job, "none", "vocals")

    assert result.pitch_backend == "adaptive_fusion"
    assert job.artifacts.melody_json.exists()
    assert job.artifacts.melody_midi.exists()
    assert job.artifacts.melody_fusion_json.exists()
    assert job.artifacts.melody_fusion_csv.exists()
    diagnostics = json.loads(job.artifacts.melody_fusion_diagnostics_json.read_text())
    assert diagnostics["fusion_status"] == "succeeded"
    assert diagnostics["succeeded_backends"] == ["rmvpe", "fcpe"]
