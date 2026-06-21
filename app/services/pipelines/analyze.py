from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.config import Settings
from app.errors import AppError
from app.models import JobStatus
from app.services.audio import prepare_analysis_audio
from app.services.key_analyzer import KeyAnalyzer
from app.services.youtube import YouTubeAdapter

if TYPE_CHECKING:
    from app.services.job_manager import Job


class AnalyzePipeline:
    def __init__(
        self,
        settings: Settings,
        youtube: YouTubeAdapter,
        analyzer: KeyAnalyzer,
    ):
        self.settings = settings
        self.youtube = youtube
        self.analyzer = analyzer

    async def run(self, job: Job) -> None:
        job.status, job.stage, job.progress = (
            JobStatus.FETCHING_METADATA,
            "fetching_metadata",
            10,
        )
        job.source_info, _raw = await self.youtube.metadata(job.youtube_url)
        job.status, job.stage, job.progress = JobStatus.DOWNLOADING, "downloading", 30
        job.stage_progress = 0

        def update_download_progress(percent: int) -> None:
            job.stage_progress = percent
            job.progress = max(job.progress, 30 + round(percent * 0.24))

        downloaded = await self.youtube.download(
            job.youtube_url,
            job.root,
            progress_callback=update_download_progress,
        )
        source = job.artifacts.source_dir / downloaded.name
        downloaded.replace(source)
        job.source_path = source
        job.stage_progress = None
        job.status, job.stage, job.progress = (
            JobStatus.PREPARING_AUDIO,
            "preparing_audio",
            55,
        )
        analysis_audio = await prepare_analysis_audio(job.source_path, job.artifacts, self.settings)
        job.status, job.stage, job.progress = JobStatus.ANALYZING, "detecting_key", 75
        try:
            job.analysis = await asyncio.wait_for(
                asyncio.to_thread(self.analyzer.analyze, analysis_audio),
                timeout=self.settings.analysis_timeout_seconds,
            )
        except (ValueError, asyncio.TimeoutError) as exc:
            raise AppError(500, "ANALYSIS_FAILED", "無法判斷歌曲調性，請嘗試其他影片。") from exc

        # Keep the normalized analysis audio until the job expires. Future beat and
        # melody subtasks can reuse it without decoding the source again.
        job.status, job.stage, job.progress = JobStatus.READY, "awaiting_selection", 100
