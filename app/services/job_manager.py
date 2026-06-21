from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.errors import AppError
from app.models import ErrorDetail, JobPublic, JobStatus, KeyAnalysisResult, OutputInfo, SourceInfo
from app.services.artifacts import JobArtifacts
from app.services.files import safe_child
from app.services.key_analyzer import LibrosaKeyAnalyzer
from app.services.key_names import shift_options
from app.services.pipelines import AnalyzePipeline, TransposePipeline
from app.services.task_queue import QueueItem, TaskQueue
from app.services.youtube import CanonicalYouTubeUrl, YouTubeAdapter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Job:
    job_id: str
    owner_id: str
    youtube_url: CanonicalYouTubeUrl
    artifacts: JobArtifacts
    created_at: datetime
    expires_at: datetime
    status: JobStatus = JobStatus.QUEUED
    stage: str = "queued"
    progress: int = 0
    stage_progress: int | None = None
    source_info: SourceInfo | None = None
    source_path: Path | None = None
    analysis: KeyAnalysisResult | None = None
    outputs: OrderedDict[tuple[int, int], Path] = field(default_factory=OrderedDict)
    active_shift: int | None = None
    active_bitrate_kbps: int | None = None
    error: ErrorDetail | None = None

    @property
    def root(self) -> Path:
        return self.artifacts.root


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.queue = TaskQueue(settings.max_queue_size)
        self.youtube = YouTubeAdapter(settings)
        self.analyzer = LibrosaKeyAnalyzer()
        self.worker_task: asyncio.Task | None = None
        self.cleanup_task: asyncio.Task | None = None
        self.running: dict[str, asyncio.Task] = {}
        self._submissions: dict[str, deque[float]] = {}

    async def start(self) -> None:
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        await self._remove_stale_directories()
        self.worker_task = asyncio.create_task(self._worker(), name="job-worker")
        self.cleanup_task = asyncio.create_task(self._cleanup_loop(), name="job-cleanup")

    async def stop(self) -> None:
        for task in (self.worker_task, self.cleanup_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self.worker_task, self.cleanup_task) if task),
            return_exceptions=True,
        )

    def check_rate_limit(self, owner_id: str) -> None:
        now = time.monotonic()
        history = self._submissions.setdefault(owner_id, deque())
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= 5:
            raise AppError(429, "RATE_LIMITED", "操作過於頻繁，請稍後再試。", True)
        history.append(now)

    async def create(self, owner_id: str, url: CanonicalYouTubeUrl) -> Job:
        self.check_rate_limit(owner_id)
        active = next(
            (
                job
                for job in self.jobs.values()
                if job.owner_id == owner_id and not self._is_terminal(job)
            ),
            None,
        )
        if active:
            if active.youtube_url.video_id == url.video_id:
                return active
            raise AppError(409, "JOB_BUSY", "目前已有歌曲正在處理，請稍候。", True)
        if self.queue.full():
            raise AppError(503, "SERVICE_BUSY", "目前處理工作較多，請稍後再試。", True)

        job_id = str(uuid.uuid4())
        root = safe_child(self.settings.work_root, job_id)
        artifacts = JobArtifacts(root)
        artifacts.create_directories()
        created = datetime.now(UTC)
        job = Job(
            job_id=job_id,
            owner_id=owner_id,
            youtube_url=url,
            artifacts=artifacts,
            created_at=created,
            expires_at=created + timedelta(minutes=self.settings.job_ttl_minutes),
        )
        self.jobs[job_id] = job
        await self.queue.put(QueueItem(job_id, "analyze"))
        return job

    def get(self, job_id: str, owner_id: str) -> Job:
        try:
            parsed = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise AppError(404, "JOB_NOT_FOUND", "暫存工作不存在或已失效。") from exc
        job = self.jobs.get(parsed)
        if not job or job.owner_id != owner_id or datetime.now(UTC) >= job.expires_at:
            raise AppError(404, "JOB_NOT_FOUND", "暫存工作不存在或已失效。")
        return job

    async def request_transpose(
        self,
        job: Job,
        semitones: int,
        bitrate_kbps: int = 192,
    ) -> Path | None:
        if semitones < -self.settings.shift_range or semitones > self.settings.shift_range:
            raise AppError(422, "INVALID_SHIFT", "請選擇畫面提供的升降半音數。")
        if bitrate_kbps not in {128, 192, 256}:
            raise AppError(422, "INVALID_BITRATE", "請選擇畫面提供的位元率。")
        output_key = (semitones, bitrate_kbps)
        existing = job.outputs.get(output_key)
        if existing and existing.exists():
            job.outputs.move_to_end(output_key)
            return existing
        if job.status == JobStatus.TRANSPOSING:
            raise AppError(409, "JOB_BUSY", "這首歌曲正在處理中，請稍候。", True)
        if job.status not in {JobStatus.READY, JobStatus.COMPLETED} or not job.source_path:
            raise AppError(409, "JOB_BUSY", "歌曲尚未完成分析，請稍候。", True)
        if self.queue.full():
            raise AppError(503, "SERVICE_BUSY", "目前處理工作較多，請稍後再試。", True)
        job.status = JobStatus.TRANSPOSING
        job.stage = "queued_transpose"
        job.progress = 0
        job.stage_progress = 0
        job.active_shift = semitones
        job.active_bitrate_kbps = bitrate_kbps
        job.error = None
        await self.queue.put(QueueItem(job.job_id, "transpose", semitones, bitrate_kbps))
        return None

    def public(self, job: Job) -> JobPublic:
        options = None
        if job.analysis:
            options = shift_options(
                job.analysis.root_index, job.analysis.mode, self.settings.shift_range
            )
        source = job.source_info
        if source and safe_child(job.root, "thumbnail.jpg").exists():
            source = source.model_copy(
                update={"thumbnail_url": f"/api/jobs/{job.job_id}/thumbnail"}
            )
        return JobPublic(
            job_id=job.job_id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            stage_progress=job.stage_progress,
            created_at=job.created_at,
            expires_at=job.expires_at,
            source=source,
            analysis=job.analysis,
            shift_options=options,
            outputs=[
                OutputInfo(semitones=semitones, bitrate_kbps=bitrate)
                for semitones, bitrate in job.outputs
            ],
            active_shift=job.active_shift,
            active_bitrate_kbps=job.active_bitrate_kbps,
            error=job.error,
        )

    async def delete(self, job: Job) -> None:
        job.status = JobStatus.CANCELLED
        running = self.running.get(job.job_id)
        if running:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        self.jobs.pop(job.job_id, None)
        await asyncio.to_thread(shutil.rmtree, job.root, True)

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                job = self.jobs.get(item.job_id)
                if not job or job.status == JobStatus.CANCELLED:
                    continue
                if item.operation == "analyze":
                    pipeline = AnalyzePipeline(self.settings, self.youtube, self.analyzer)
                    operation = asyncio.create_task(pipeline.run(job))
                else:
                    assert item.semitones is not None and item.bitrate_kbps is not None
                    pipeline = TransposePipeline(self.settings)
                    operation = asyncio.create_task(
                        pipeline.run(job, item.semitones, item.bitrate_kbps)
                    )
                self.running[job.job_id] = operation
                await operation
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current and current.cancelling():
                    raise
                # A DELETE request cancelled only the current job operation;
                # keep the single queue worker alive for subsequent jobs.
                continue
            except AppError as exc:
                job = self.jobs.get(item.job_id)
                if job:
                    job.status = JobStatus.FAILED
                    job.stage = "failed"
                    job.error = ErrorDetail(
                        code=exc.code, message=exc.message, retryable=exc.retryable
                    )
                    logger.warning(
                        "job failed job_id=%s stage=%s error_code=%s",
                        job.job_id,
                        job.stage,
                        exc.code,
                        exc_info=True,
                    )
            except Exception:
                job = self.jobs.get(item.job_id)
                if job:
                    job.status = JobStatus.FAILED
                    job.stage = "failed"
                    job.error = ErrorDetail(
                        code="INTERNAL_ERROR", message="處理工作失敗，請重新嘗試。"
                    )
                logger.exception("unexpected job failure job_id=%s", item.job_id)
            finally:
                self.running.pop(item.job_id, None)
                self.queue.task_done()

    def _is_terminal(self, job: Job) -> bool:
        return job.status in {
            JobStatus.READY,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            now = datetime.now(UTC)
            expired = [job for job in self.jobs.values() if now >= job.expires_at]
            for job in expired:
                job.status = JobStatus.EXPIRED
                running = self.running.get(job.job_id)
                if running:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                self.jobs.pop(job.job_id, None)
                await asyncio.to_thread(shutil.rmtree, job.root, True)

    async def _remove_stale_directories(self) -> None:
        now = time.time()
        ttl_seconds = self.settings.job_ttl_minutes * 60
        for path in self.settings.work_root.iterdir():
            try:
                if path.is_dir() and now - path.stat().st_mtime >= ttl_seconds:
                    await asyncio.to_thread(shutil.rmtree, path, True)
            except OSError:
                logger.warning("failed to inspect stale work directory")
