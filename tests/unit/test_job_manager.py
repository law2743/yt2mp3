from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyStatus
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

        async def run(self, _job, _meter_hint):
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
        # Discard the queued analyze operation before adding melody.
        analyze_item = await manager.queue.get()
        assert analyze_item.operation == "analyze"
        manager.queue.task_done()
        await manager.request_melody(ready, False, "auto")
        await manager.start()
        await manager.queue.join()
        assert ready.status == JobStatus.READY
        assert ready.melody.status == MelodyStatus.FAILED
        assert ready.melody.error.code == "MELODY_ANALYSIS_FAILED"
    finally:
        await manager.stop()
