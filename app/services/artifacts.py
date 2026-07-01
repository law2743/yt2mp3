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
    def melody_json(self) -> Path:
        return safe_child(self.analysis_dir, "melody.json")

    @property
    def melody_midi(self) -> Path:
        return safe_child(self.analysis_dir, "melody.mid")

    @property
    def stems_dir(self) -> Path:
        return safe_child(self.analysis_dir, "stems")

    @property
    def vocals_wav(self) -> Path:
        return safe_child(self.stems_dir, "vocals.wav")

    @property
    def accompaniment_wav(self) -> Path:
        return safe_child(self.stems_dir, "accompaniment.wav")

    def stem_mp3(self, stem: str, bitrate_kbps: int) -> Path:
        if stem not in {"vocals", "accompaniment"}:
            raise ValueError("unsupported stem")
        if bitrate_kbps not in {128, 192, 256}:
            raise ValueError("unsupported bitrate")
        return safe_child(self.stems_dir, f"{stem}_{bitrate_kbps}k.mp3")

    @property
    def stems_metadata_json(self) -> Path:
        return safe_child(self.stems_dir, "metadata.json")

    @property
    def pitch_dir(self) -> Path:
        return safe_child(self.analysis_dir, "pitch")

    @property
    def vocal_pitch_json(self) -> Path:
        return safe_child(self.pitch_dir, "vocal_pitch.json")

    @property
    def rhythm_dir(self) -> Path:
        return safe_child(self.analysis_dir, "rhythm")

    @property
    def rhythm_beat_grid_json(self) -> Path:
        return safe_child(self.rhythm_dir, "beat_grid.json")

    @property
    def rhythm_vocal_onsets_csv(self) -> Path:
        return safe_child(self.rhythm_dir, "vocal_onsets.csv")

    @property
    def rhythm_notes_draft_json(self) -> Path:
        return safe_child(self.rhythm_dir, "notes_draft.json")

    @property
    def rhythm_notes_draft_csv(self) -> Path:
        return safe_child(self.rhythm_dir, "notes_draft.csv")

    @property
    def rhythm_numbered_notation_json(self) -> Path:
        return safe_child(self.rhythm_dir, "numbered_notation.json")

    @property
    def rhythm_jianpu_draft_txt(self) -> Path:
        return safe_child(self.rhythm_dir, "jianpu_draft.txt")

    @property
    def rhythm_diagnostics_json(self) -> Path:
        return safe_child(self.rhythm_dir, "rhythm_diagnostics.json")

    @property
    def melody_dir(self) -> Path:
        return safe_child(self.analysis_dir, "melody")

    @property
    def melody_fusion_dir(self) -> Path:
        return safe_child(self.melody_dir, "fusion")

    @property
    def melody_fusion_inputs_dir(self) -> Path:
        return safe_child(self.melody_fusion_dir, "inputs")

    def melody_fusion_input_csv(self, backend: str) -> Path:
        if backend not in {"rmvpe", "torchcrepe", "fcpe", "pesto"}:
            raise ValueError("unsupported pitch backend")
        return safe_child(self.melody_fusion_inputs_dir, f"{backend}.csv")

    @property
    def melody_fusion_csv(self) -> Path:
        return safe_child(self.melody_fusion_dir, "fusion.csv")

    @property
    def melody_fusion_json(self) -> Path:
        return safe_child(self.melody_fusion_dir, "fusion.json")

    @property
    def melody_fusion_diagnostics_json(self) -> Path:
        return safe_child(self.melody_fusion_dir, "diagnostics.json")

    @property
    def melody_comparison_csv(self) -> Path:
        return safe_child(self.melody_fusion_dir, "comparison.csv")

    @property
    def melody_postprocessed_csv(self) -> Path:
        return safe_child(self.melody_fusion_dir, "postprocessed.csv")

    @property
    def melody_postprocessed_json(self) -> Path:
        return safe_child(self.melody_fusion_dir, "postprocessed.json")

    @property
    def melody_postprocess_diagnostics_json(self) -> Path:
        return safe_child(self.melody_fusion_dir, "postprocess_diagnostics.json")

    @property
    def vocals_mono_16000_wav(self) -> Path:
        return safe_child(self.melody_fusion_dir, "vocals_mono_16000.wav")

    def melody_variant_json(self, source: str) -> Path:
        if source != "vocals":
            raise ValueError("unsupported melody source")
        return safe_child(self.melody_dir, "vocals_adaptive_fusion.json")

    def melody_variant_midi(self, source: str) -> Path:
        if source != "vocals":
            raise ValueError("unsupported melody source")
        return safe_child(self.melody_dir, "vocals_adaptive_fusion.mid")

    @property
    def thumbnail(self) -> Path:
        # Kept at the job root while the YouTube adapter owns thumbnail download.
        return safe_child(self.root, "thumbnail.jpg")

    def transposed_mp3(self, semitones: int, bitrate_kbps: int) -> Path:
        return safe_child(self.output_dir, f"shift_{semitones}_{bitrate_kbps}k.mp3")
