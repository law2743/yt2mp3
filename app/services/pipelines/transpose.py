from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import Settings
from app.models import JobStatus
from app.services.audio import transpose_audio
from app.services.key_names import display_key

if TYPE_CHECKING:
    from app.services.job_manager import Job


class TransposePipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, job: Job, semitones: int, bitrate_kbps: int) -> None:
        assert job.analysis and job.source_info and job.source_path
        job.stage, job.progress = "transposing", 40

        def update_transpose_progress(percent: int) -> None:
            job.stage_progress = percent
            job.progress = percent

        target_key = display_key(job.analysis.root_index + semitones, job.analysis.mode)
        output = await transpose_audio(
            job.source_path,
            job.root,
            semitones,
            job.source_info.title,
            job.source_info.uploader,
            target_key,
            self.settings,
            bitrate_kbps=bitrate_kbps,
            progress_callback=update_transpose_progress,
        )
        output_key = (semitones, bitrate_kbps)
        job.outputs[output_key] = output
        job.outputs.move_to_end(output_key)
        while len(job.outputs) > 2:
            _old_shift, old_path = job.outputs.popitem(last=False)
            old_path.unlink(missing_ok=True)
        job.status, job.stage, job.progress = JobStatus.COMPLETED, "completed", 100
        job.stage_progress = None
        job.active_shift = None
        job.active_bitrate_kbps = None
