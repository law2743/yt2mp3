from __future__ import annotations

import logging
from pathlib import Path

from app.services.artifacts import JobArtifacts
from app.services.rhythm.numbered_notation import (
    build_numbered_notation,
    write_jianpu_draft_txt,
    write_numbered_notation_json,
)
from app.services.rhythm.pipeline import run_rhythm_pipeline

logger = logging.getLogger(__name__)


def try_generate_notation_artifacts(
    job_dir: Path,
    *,
    meter_hint: str = "auto",
    key: str | None = None,
    mode: str | None = None,
    force: bool = False,
) -> bool:
    artifacts = JobArtifacts(job_dir)
    if not key or not mode:
        logger.warning(
            "notation generation missing key/mode job_dir=%s key=%s mode=%s",
            job_dir,
            key,
            mode,
        )

    try:
        run_rhythm_pipeline(job_dir, meter_hint=meter_hint, force=force)
        result = build_numbered_notation(
            artifacts.rhythm_notes_draft_json,
            key=key,
            mode=mode,
        )
        write_numbered_notation_json(result, artifacts.rhythm_numbered_notation_json)
        write_jianpu_draft_txt(result, artifacts.rhythm_jianpu_draft_txt)
    except Exception:
        logger.exception(
            "notation generation failed job_dir=%s meter_hint=%s key=%s mode=%s",
            job_dir,
            meter_hint,
            key,
            mode,
        )
        return False

    return (
        artifacts.rhythm_numbered_notation_json.exists()
        and artifacts.rhythm_jianpu_draft_txt.exists()
    )
