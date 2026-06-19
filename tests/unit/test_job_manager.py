from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus
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
