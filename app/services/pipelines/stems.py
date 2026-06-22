from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import Settings
from app.models.stem import StemSeparationMetadata
from app.services.model_backends.demucs_backend import DemucsStemSeparator
from app.services.stem_separator import StemSeparationRequest

if TYPE_CHECKING:
    from app.services.job_manager import Job

_FALLBACK_WARNING = (
    "Stem separation is unavailable; no stem artifacts were created and melody analysis "
    "will continue to use the full-mix fallback."
)


def _write_metadata(path: Path, metadata: StemSeparationMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".metadata.json.tmp")
    temporary.write_text(
        json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_stem_metadata(path: Path) -> StemSeparationMetadata | None:
    try:
        return StemSeparationMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class StemPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _fallback(self, job: Job, status: str, warning: str, error: str | None = None):
        metadata = StemSeparationMetadata(
            status=status,
            backend="none",
            source_path=job.source_path.relative_to(job.root).as_posix(),
            warnings=[warning, _FALLBACK_WARNING] if warning != _FALLBACK_WARNING else [warning],
            error=error,
        )
        _write_metadata(job.artifacts.stems_metadata_json, metadata)
        return metadata

    async def run(self, job: Job, force: bool = False) -> StemSeparationMetadata:
        assert job.source_path
        artifacts = job.artifacts
        artifacts.stems_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings.stem_separation_enabled:
            return self._fallback(job, "skipped", "Stem separation is disabled.")

        cached = read_stem_metadata(artifacts.stems_metadata_json)
        valid_cache = (
            cached
            and cached.status == "completed"
            and artifacts.vocals_wav.is_file()
            and artifacts.vocals_wav.stat().st_size > 44
            and artifacts.accompaniment_wav.is_file()
            and artifacts.accompaniment_wav.stat().st_size > 44
        )
        if self.settings.stem_cache_enabled and not force and valid_cache:
            cached = cached.model_copy(update={"cached": True})
            _write_metadata(artifacts.stems_metadata_json, cached)
            return cached

        artifacts.vocals_wav.unlink(missing_ok=True)
        artifacts.accompaniment_wav.unlink(missing_ok=True)
        if self.settings.stem_separation_backend == "none":
            return self._fallback(job, "fallback", _FALLBACK_WARNING)

        request = StemSeparationRequest(
            job_id=job.job_id,
            job_root=job.root,
            source_audio=job.source_path,
            stems_dir=artifacts.stems_dir,
            vocals_output=artifacts.vocals_wav,
            accompaniment_output=artifacts.accompaniment_wav,
            metadata_output=artifacts.stems_metadata_json,
            force=force,
        )
        try:
            metadata = await DemucsStemSeparator(self.settings).separate(request)
        except Exception as exc:
            message = str(exc).replace(str(job.root), "<job>").replace(str(Path.home()), "~")[:500]
            return self._fallback(job, "fallback", message, message)
        _write_metadata(artifacts.stems_metadata_json, metadata)
        return metadata
