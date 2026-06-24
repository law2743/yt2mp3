from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.config import Settings
from app.errors import AppError
from app.models.melody import MelodySourceUsed
from app.models.vocal_pitch import VocalPitchResult
from app.services.model_backends.rmvpe_backend import RmvpePitchBackend

if TYPE_CHECKING:
    from app.services.job_manager import Job


@dataclass(frozen=True, slots=True)
class VocalPitchEnsureResult:
    status: Literal["completed", "cached", "skipped"]
    result: VocalPitchResult | None = None


async def ensure_vocal_pitch(
    job: Job,
    source_used: MelodySourceUsed,
    settings: Settings,
) -> VocalPitchEnsureResult:
    if source_used == "mix":
        return VocalPitchEnsureResult(status="skipped")
    if not job.artifacts.vocals_wav.exists():
        raise AppError(
            422,
            "VOCALS_SOURCE_NOT_READY",
            "人聲 stem 尚未產生，無法執行 RMVPE vocal pitch 分析。",
        )

    output = job.artifacts.vocal_pitch_json
    if output.exists():
        try:
            result = VocalPitchResult.model_validate_json(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            output.unlink(missing_ok=True)
        else:
            return VocalPitchEnsureResult(status="cached", result=result)

    job.artifacts.pitch_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = await RmvpePitchBackend(settings).extract(job.artifacts.vocals_wav, output)
    except Exception as exc:
        output.unlink(missing_ok=True)
        message = str(exc).replace(str(job.root), "<job>")[:500]
        raise AppError(
            500,
            "PITCH_FAILED",
            f"RMVPE vocal pitch 分析失敗：{message}",
            True,
        ) from exc
    return VocalPitchEnsureResult(status="completed", result=result)
