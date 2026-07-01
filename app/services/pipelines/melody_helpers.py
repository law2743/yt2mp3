from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from app.errors import AppError
from app.models.melody import MelodySource, MelodySourceUsed

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
