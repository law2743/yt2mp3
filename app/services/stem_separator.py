from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models.stem import StemSeparationMetadata


@dataclass(frozen=True, slots=True)
class StemSeparationRequest:
    job_id: str
    job_root: Path
    source_audio: Path
    stems_dir: Path
    vocals_output: Path
    accompaniment_output: Path
    metadata_output: Path
    force: bool = False


class StemSeparator(Protocol):
    backend_name: str

    async def separate(self, request: StemSeparationRequest) -> StemSeparationMetadata: ...
