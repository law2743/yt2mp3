import asyncio
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.config import Settings
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate, SourceInfo
from app.services.job_manager import JobManager
from app.services.youtube import canonicalize_youtube_url


class FakeYouTube:
    async def metadata(self, url):
        return SourceInfo(
            video_id=url.video_id,
            title="Authorized fixture",
            uploader="Tests",
            duration_seconds=8,
        ), {}

    async def download(self, _url, job_root: Path, progress_callback=None):
        if progress_callback:
            progress_callback(100)
        sample_rate = 22050
        chunks = []
        for root in (0, 5, 7, 0):
            time = np.arange(sample_rate * 2) / sample_rate
            chord = (
                sum(
                    np.sin(2 * np.pi * 261.6256 * 2 ** ((root + interval) / 12) * time)
                    for interval in (0, 4, 7)
                )
                / 3
            )
            chunks.append(chord * 0.25)
        path = job_root / "source.wav"
        sf.write(path, np.concatenate(chunks), sample_rate)
        return path


class FakeKeyAnalyzer:
    def analyze(self, _audio_path):
        return KeyAnalysisResult(
            root_index=0,
            root_name="C",
            mode="major",
            display_name="C Major",
            confidence=0.8,
            candidates=[KeyCandidate(key="C Major", score=1.0)],
            algorithm_version="integration-fixture-v1",
        )


@pytest.mark.asyncio
async def test_mocked_end_to_end_pipeline(tmp_path):
    settings = Settings(
        app_env="test",
        work_root=tmp_path,
        analysis_timeout_seconds=60,
        transpose_timeout_seconds=60,
    )
    manager = JobManager(settings)
    manager.youtube = FakeYouTube()
    # The real DSP analyzer has dedicated fixture tests. This integration test
    # covers orchestration without tying its runtime to numba/JIT warm-up.
    manager.analyzer = FakeKeyAnalyzer()
    await manager.start()
    try:
        url = canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        job = await manager.create("test-owner", url)
        await asyncio.wait_for(manager.queue.join(), 90)
        assert job.status == JobStatus.READY
        assert job.source_path is not None
        assert job.source_path.parent == job.artifacts.source_dir
        assert job.artifacts.analysis_audio.exists()

        await manager.request_transpose(job, 1)
        await asyncio.wait_for(manager.queue.join(), 90)
        assert job.status == JobStatus.COMPLETED
        assert job.outputs[(1, 192)].stat().st_size > 0

        cached = await manager.request_transpose(job, 1)
        assert cached == job.outputs[(1, 192)]
    finally:
        await manager.stop()
