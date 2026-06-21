from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings
from app.errors import AppError
from app.models.melody import MelodyAnalysisResult, MelodyStatus, MeterHint
from app.services.melody import analyze_melody

if TYPE_CHECKING:
    from app.services.job_manager import Job


def _worker(source: Path, json_output: Path, midi_output: Path, kwargs: dict) -> None:
    analyze_melody(source, json_output, midi_output, **kwargs)


class MelodyPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, job: Job, meter_hint: MeterHint) -> MelodyAnalysisResult:
        assert job.analysis
        source = job.artifacts.analysis_audio
        if not source.exists():
            raise AppError(422, "MELODY_SOURCE_NOT_READY", "請先完成歌曲分析後再產生主旋律。")

        job.melody.status = MelodyStatus.PREPARING
        job.melody.stage = "preparing"
        job.melody.progress = 5
        temporary_json = job.artifacts.analysis_dir / ".melody.json.tmp"
        temporary_midi = job.artifacts.analysis_dir / ".melody.mid.tmp"
        for path in (temporary_json, temporary_midi):
            path.unlink(missing_ok=True)

        kwargs = {
            "job_id": job.job_id,
            "key": job.analysis.display_name,
            "root_index": job.analysis.root_index,
            "mode": job.analysis.mode,
            "meter_hint": meter_hint,
            "min_note_duration_sec": self.settings.melody_min_note_duration_sec,
            "max_gap_merge_sec": self.settings.melody_max_gap_merge_sec,
            "min_confidence": self.settings.melody_min_confidence,
            "fmin": self.settings.melody_fmin,
            "fmax": self.settings.melody_fmax,
            "max_notes": self.settings.melody_max_notes,
        }
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_worker,
            args=(source, temporary_json, temporary_midi, kwargs),
            daemon=True,
        )
        job.melody.status = MelodyStatus.DETECTING
        job.melody.stage = "extracting_pitch"
        job.melody.progress = 20
        process.start()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(process.join), timeout=self.settings.melody_timeout_seconds
            )
        except TimeoutError as exc:
            process.terminate()
            await asyncio.to_thread(process.join, 5)
            raise AppError(
                504,
                "MELODY_PROCESS_TIMEOUT",
                "主旋律分析時間超過限制，請改用較短或較清楚的音訊。",
                True,
            ) from exc
        finally:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)

        job.melody.status = MelodyStatus.EXPORTING
        job.melody.stage = "exporting"
        job.melody.progress = 90
        if process.exitcode != 0 or not temporary_json.exists() or not temporary_midi.exists():
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)
            raise AppError(500, "MELODY_ANALYSIS_FAILED", "無法產生主旋律草稿，請稍後再試。", True)
        try:
            result = MelodyAnalysisResult.model_validate_json(
                temporary_json.read_text(encoding="utf-8")
            )
            temporary_json.replace(job.artifacts.melody_json)
            temporary_midi.replace(job.artifacts.melody_midi)
        except Exception as exc:
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)
            raise AppError(500, "MELODY_EXPORT_FAILED", "主旋律檔案輸出失敗。", True) from exc
        return result
