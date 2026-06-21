from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.files import safe_child


@dataclass(frozen=True, slots=True)
class JobArtifacts:
    """Owns the on-disk layout of one temporary job."""

    root: Path

    def create_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=False)
        self.source_dir.mkdir()
        self.analysis_dir.mkdir()
        self.output_dir.mkdir()

    @property
    def source_dir(self) -> Path:
        return safe_child(self.root, "source")

    @property
    def analysis_dir(self) -> Path:
        return safe_child(self.root, "analysis")

    @property
    def output_dir(self) -> Path:
        return safe_child(self.root, "output")

    @property
    def analysis_audio(self) -> Path:
        return safe_child(self.analysis_dir, "mono-22050.wav")

    @property
    def thumbnail(self) -> Path:
        # Kept at the job root while the YouTube adapter owns thumbnail download.
        return safe_child(self.root, "thumbnail.jpg")

    def transposed_mp3(self, semitones: int, bitrate_kbps: int) -> Path:
        return safe_child(self.output_dir, f"shift_{semitones}_{bitrate_kbps}k.mp3")
