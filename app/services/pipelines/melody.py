from __future__ import annotations

import asyncio
import multiprocessing
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings
from app.errors import AppError
from app.models.melody import (
    MelodyAnalysisResult,
    MelodySource,
    MelodySourceUsed,
    MelodyStatus,
    MeterHint,
)
from app.services.melody import analyze_rmvpe_melody
from app.services.pipelines.stems import read_stem_metadata
from app.services.pipelines.vocal_pitch import ensure_vocal_pitch

if TYPE_CHECKING:
    from app.services.job_manager import Job


def resolve_melody_source(job: Job, requested: MelodySource) -> tuple[MelodySourceUsed, Path]:
    if not job.artifacts.vocals_wav.exists():
        raise AppError(
            422,
            "VOCALS_SOURCE_NOT_READY",
            "人聲 stem 尚未產生，請先完成 人聲／伴奏 分離。",
        )
    if requested not in {"auto", "vocals"}:
        raise AppError(422, "MELODY_SOURCE_UNSUPPORTED", "主旋律分析目前僅支援人聲來源。")
    return "vocals", job.artifacts.vocals_wav


def sync_best_melody_alias(
    job: Job, priority: tuple[MelodySourceUsed, ...] = ("vocals",)
) -> None:
    preferred = next(
        (
            source
            for source in priority
            if job.artifacts.melody_variant_json(source).exists()
            and job.artifacts.melody_variant_midi(source).exists()
        ),
        "vocals",
    )
    json_source = job.artifacts.melody_variant_json(preferred)
    midi_source = job.artifacts.melody_variant_midi(preferred)
    if json_source.exists() and midi_source.exists():
        temporary_json = job.artifacts.analysis_dir / ".melody.alias.json.tmp"
        temporary_midi = job.artifacts.analysis_dir / ".melody.alias.mid.tmp"
        try:
            shutil.copyfile(json_source, temporary_json)
            shutil.copyfile(midi_source, temporary_midi)
            temporary_json.replace(job.artifacts.melody_json)
            temporary_midi.replace(job.artifacts.melody_midi)
        finally:
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)


def _worker(source: Path, json_output: Path, midi_output: Path, kwargs: dict) -> None:
    vocal_pitch_path = kwargs.pop("vocal_pitch_path", None)
    if not vocal_pitch_path:
        raise RuntimeError("RMVPE vocal pitch artifact is required")
    analyze_rmvpe_melody(source, Path(vocal_pitch_path), json_output, midi_output, **kwargs)


class MelodyPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(
        self, job: Job, meter_hint: MeterHint, requested_source: MelodySource = "auto"
    ) -> MelodyAnalysisResult:
        assert job.analysis
        source_used, source = resolve_melody_source(job, requested_source)
        if not source.exists():
            raise AppError(422, "MELODY_SOURCE_NOT_READY", "請先完成歌曲分析後再產生主旋律。")

        job.melody.status = MelodyStatus.PREPARING
        job.melody.stage = "preparing"
        job.melody.progress = 5
        job.artifacts.melody_dir.mkdir(parents=True, exist_ok=True)
        temporary_json = job.artifacts.melody_dir / f".{source_used}_rmvpe.json.tmp"
        temporary_midi = job.artifacts.melody_dir / f".{source_used}_rmvpe.mid.tmp"
        for path in (temporary_json, temporary_midi):
            path.unlink(missing_ok=True)

        stem_metadata = read_stem_metadata(job.artifacts.stems_metadata_json)
        source_audio_path = source.relative_to(job.root).as_posix()
        kwargs = {
            "job_id": job.job_id,
            "key": job.analysis.display_name,
            "root_index": job.analysis.root_index,
            "mode": job.analysis.mode,
            "meter_hint": meter_hint,
            "min_note_duration_sec": self.settings.melody_min_note_duration_sec,
            "max_gap_merge_sec": self.settings.melody_max_gap_merge_sec,
            "min_confidence": self.settings.melody_min_confidence,
            "max_notes": self.settings.melody_max_notes,
            "requested_source": requested_source,
            "melody_source_used": source_used,
            "source_audio_path": source_audio_path,
            "beat_reference": job.artifacts.analysis_audio,
            "separation_backend": stem_metadata.backend
            if source_used == "vocals" and stem_metadata
            else None,
            "separation_status": stem_metadata.status
            if source_used == "vocals" and stem_metadata
            else "missing",
        }

        job.melody.status = MelodyStatus.DETECTING
        job.melody.stage = "extracting_vocal_pitch"
        job.melody.progress = 1
        await ensure_vocal_pitch(job, source_used, self.settings)
        kwargs["vocal_pitch_path"] = str(job.artifacts.vocal_pitch_json)

        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_worker,
            args=(source, temporary_json, temporary_midi, kwargs),
            daemon=True,
        )
        job.melody.status = MelodyStatus.DETECTING
        job.melody.stage = "extracting_pitch"
        job.melody.progress = 1
        if self.settings.app_env == "test":
            _worker(source, temporary_json, temporary_midi, kwargs)
            process_exitcode = 0
        else:
            process.start()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(process.join),
                    timeout=self.settings.melody_timeout_seconds,
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
            process_exitcode = process.exitcode

        job.melody.status = MelodyStatus.EXPORTING
        job.melody.stage = "exporting"
        job.melody.progress = 1
        if process_exitcode != 0 or not temporary_json.exists() or not temporary_midi.exists():
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)
            raise AppError(500, "MELODY_ANALYSIS_FAILED", "無法產生主旋律草稿，請稍後再試。", True)
        try:
            result = MelodyAnalysisResult.model_validate_json(
                temporary_json.read_text(encoding="utf-8")
            )
            temporary_json.replace(job.artifacts.melody_variant_json(source_used))
            temporary_midi.replace(job.artifacts.melody_variant_midi(source_used))
            priority = tuple(self.settings.melody_source_priority.split(","))
            sync_best_melody_alias(job, priority)
        except Exception as exc:
            temporary_json.unlink(missing_ok=True)
            temporary_midi.unlink(missing_ok=True)
            raise AppError(500, "MELODY_EXPORT_FAILED", "主旋律檔案輸出失敗。", True) from exc
        return result
