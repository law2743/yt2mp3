import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyAnalysisResult, MelodyStatus, MelodySummary
from app.services.job_manager import JobManager
from app.services.youtube import canonicalize_youtube_url


@pytest.mark.asyncio
async def test_rate_limit_and_duplicate_job(tmp_path):
    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    first = await manager.create("owner", url)
    duplicate = await manager.create("owner", url)
    assert duplicate is first

    for _ in range(3):
        manager.check_rate_limit("other")
    manager.check_rate_limit("other")
    manager.check_rate_limit("other")
    with pytest.raises(AppError) as error:
        manager.check_rate_limit("other")
    assert error.value.code == "RATE_LIMITED"


def test_expired_or_foreign_job_is_hidden(tmp_path):
    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    # Construct through create so UUID and safe directory behavior are exercised.
    job = __import__("asyncio").run(
        manager.create("owner", canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"))
    )
    with pytest.raises(AppError):
        manager.get(job.job_id, "different-owner")
    job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AppError):
        manager.get(job.job_id, "owner")


def test_status_values_match_public_contract():
    assert JobStatus.READY.value == "ready"
    assert JobStatus.TRANSPOSING.value == "transposing"


@pytest.mark.asyncio
async def test_melody_failure_does_not_change_main_job_status(tmp_path, monkeypatch):
    from app.errors import AppError
    import app.services.job_manager as job_manager_module

    class FailingMelodyPipeline:
        def __init__(self, _settings):
            pass

        async def run(self, _job, _meter_hint, _source="auto"):
            raise AppError(500, "MELODY_ANALYSIS_FAILED", "fixture failure", True)

    monkeypatch.setattr(job_manager_module, "MelodyPipeline", FailingMelodyPipeline)
    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    try:
        url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        ready = await manager.create("owner", url)
        ready.status = JobStatus.READY
        ready.stage = "awaiting_selection"
        ready.analysis = KeyAnalysisResult(
            root_index=0,
            root_name="C",
            mode="major",
            display_name="C Major",
            confidence=0.8,
            candidates=[KeyCandidate(key="C Major", score=1)],
            algorithm_version="fixture",
        )
        ready.artifacts.analysis_audio.write_bytes(b"fixture")
        ready.artifacts.stems_dir.mkdir()
        ready.artifacts.vocals_wav.write_bytes(b"vocals")
        # Discard the queued analyze operation before adding melody.
        analyze_item = await manager.queue.get()
        assert analyze_item.operation == "analyze"
        manager.queue.task_done()
        await manager.request_melody(ready, False, "auto")
        worker = asyncio.create_task(manager._worker())
        await manager.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert ready.status == JobStatus.READY
        assert ready.melody.status == MelodyStatus.FAILED
        assert ready.melody.error.code == "MELODY_ANALYSIS_FAILED"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_melody_success_calls_notation_generation_without_blocking_status(
    tmp_path,
    monkeypatch,
):
    import app.services.job_manager as job_manager_module

    calls = []

    class SuccessfulMelodyPipeline:
        def __init__(self, _settings):
            pass

        async def run(self, job, meter_hint, _source="auto"):
            job.artifacts.melody_json.write_text("melody-json", encoding="utf-8")
            job.artifacts.melody_midi.write_bytes(b"MThd")
            return MelodyAnalysisResult(
                job_id=job.job_id,
                key="C Major",
                mode="major",
                meter_hint=meter_hint,
                pitch_backend="adaptive_fusion",
                is_fallback=False,
                notes=[],
                summary=MelodySummary(
                    note_count=0,
                    voiced_ratio=0,
                    average_confidence=0,
                ),
            )

    def fake_notation(job_dir, *, meter_hint="auto", key=None, mode=None, force=False):
        calls.append(
            {
                "job_dir": job_dir,
                "meter_hint": meter_hint,
                "key": key,
                "mode": mode,
                "force": force,
            }
        )
        assert (job_dir / "analysis" / "melody.json").read_text(encoding="utf-8") == "melody-json"
        assert (job_dir / "analysis" / "melody.mid").read_bytes() == b"MThd"
        return False

    async def inline_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(job_manager_module, "MelodyPipeline", SuccessfulMelodyPipeline)
    monkeypatch.setattr(job_manager_module, "try_generate_notation_artifacts", fake_notation)
    monkeypatch.setattr(job_manager_module.asyncio, "to_thread", inline_to_thread)

    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    try:
        url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        ready = await manager.create("owner", url)
        ready.status = JobStatus.READY
        ready.stage = "awaiting_selection"
        ready.analysis = KeyAnalysisResult(
            root_index=0,
            root_name="C",
            mode="major",
            display_name="C Major",
            confidence=0.8,
            candidates=[KeyCandidate(key="C Major", score=1)],
            algorithm_version="fixture",
        )
        ready.artifacts.analysis_audio.write_bytes(b"fixture")
        ready.artifacts.stems_dir.mkdir()
        ready.artifacts.vocals_wav.write_bytes(b"vocals")

        analyze_item = await manager.queue.get()
        assert analyze_item.operation == "analyze"
        manager.queue.task_done()
        await manager.request_melody(ready, False, "auto")
        worker = asyncio.create_task(manager._worker())
        await manager.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        assert calls == [
            {
                "job_dir": ready.root,
                "meter_hint": "auto",
                "key": "C",
                "mode": "major",
                "force": False,
            }
        ]
        assert ready.status == JobStatus.READY
        assert ready.melody.status == MelodyStatus.COMPLETED
        assert ready.melody.error is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_cached_melody_request_generates_missing_notation_artifacts(
    tmp_path,
    monkeypatch,
):
    import app.services.job_manager as job_manager_module

    calls = []

    def fake_notation(job_dir, *, meter_hint="auto", key=None, mode=None, force=False):
        calls.append(
            {
                "job_dir": job_dir,
                "meter_hint": meter_hint,
                "key": key,
                "mode": mode,
                "force": force,
            }
        )
        assert (job_dir / "analysis" / "melody.json").exists()
        assert (job_dir / "analysis" / "melody.mid").exists()
        return True

    async def inline_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(job_manager_module, "try_generate_notation_artifacts", fake_notation)
    monkeypatch.setattr(job_manager_module.asyncio, "to_thread", inline_to_thread)

    manager = JobManager(Settings(app_env="test", work_root=tmp_path))
    try:
        url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        ready = await manager.create("owner", url)
        ready.status = JobStatus.READY
        ready.stage = "awaiting_selection"
        ready.analysis = KeyAnalysisResult(
            root_index=0,
            root_name="C",
            mode="major",
            display_name="C Major",
            confidence=0.8,
            candidates=[KeyCandidate(key="C Major", score=1)],
            algorithm_version="fixture",
        )
        ready.artifacts.analysis_audio.write_bytes(b"fixture")
        ready.artifacts.stems_dir.mkdir()
        ready.artifacts.vocals_wav.write_bytes(b"vocals")

        result = MelodyAnalysisResult(
            job_id=ready.job_id,
            key="C Major",
            mode="major",
            meter_hint="auto",
            pitch_backend="adaptive_fusion",
            is_fallback=False,
            notes=[],
            summary=MelodySummary(
                note_count=0,
                voiced_ratio=0,
                average_confidence=0,
            ),
        )
        ready.artifacts.melody_dir.mkdir(parents=True, exist_ok=True)
        ready.artifacts.melody_variant_json("vocals").write_text(
            result.model_dump_json(),
            encoding="utf-8",
        )
        ready.artifacts.melody_variant_midi("vocals").write_bytes(b"MThd")

        cached = await manager.request_melody(ready, False, "auto")

        assert cached is True
        assert calls == [
            {
                "job_dir": ready.root,
                "meter_hint": "auto",
                "key": "C",
                "mode": "major",
                "force": False,
            }
        ]
        assert ready.melody.status == MelodyStatus.COMPLETED
        assert ready.melody.error is None
    finally:
        await manager.stop()
