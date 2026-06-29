from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.models.rhythm import BeatGridResult, NoteDraftResult, RhythmDiagnostics, VocalOnsetEvent
from app.services.artifacts import JobArtifacts
from app.services.rhythm.beat_grid import analyze_beat_grid
from app.services.rhythm.note_draft import (
    ALGORITHM_VERSION as NOTE_DRAFT_ALGORITHM_VERSION,
    build_note_draft,
    write_note_draft_csv,
    write_note_draft_json,
)
from app.services.rhythm.vocal_onset import analyze_vocal_onsets, write_vocal_onsets_csv

ALGORITHM_VERSION = "rhythm-pipeline-v1"
PITCH_TIMELINE_CANDIDATES = (
    ("melody_fusion_csv", "fusion.csv"),
    ("melody_fusion_json", "fusion.json"),
    ("melody/fusion/hybrid_postprocessed.csv", "hybrid_postprocessed.csv"),
    ("melody/fusion/comparison.csv", "comparison.csv"),
    ("melody/fusion/diagnostics.csv", "diagnostics.csv"),
    ("melody/fusion/melody_fusion.csv", "melody_fusion.csv"),
    ("pitch/hybrid_postprocessed.csv", "hybrid_postprocessed.csv"),
    ("pitch/comparison.csv", "comparison.csv"),
    ("pitch/fusion.csv", "fusion.csv"),
)


def run_rhythm_pipeline(
    job_dir: Path,
    *,
    meter_hint: str = "auto",
    force: bool = False,
) -> NoteDraftResult:
    artifacts = JobArtifacts(job_dir)
    artifacts.rhythm_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    beat_grid, beat_reused = _ensure_beat_grid(artifacts, meter_hint=meter_hint, force=force)
    warnings.extend(_prefixed_warnings("beat_grid", beat_grid.warnings))

    vocal_onsets, onset_reused, onset_step_warnings = _ensure_vocal_onsets(artifacts, force=force)
    warnings.extend(onset_step_warnings)
    warnings.extend(_prefixed_warnings("vocal_onset", _collect_onset_warnings(vocal_onsets)))

    pitch_timeline = _resolve_pitch_timeline(artifacts)
    if pitch_timeline is None:
        pitch_timeline = artifacts.melody_fusion_csv
        warnings.append("missing_pitch_timeline")

    result = _ensure_note_draft(
        artifacts,
        pitch_timeline=pitch_timeline,
        force=force,
        warnings=warnings,
    )
    warnings.extend(warning for warning in result.warnings if warning not in warnings)

    diagnostics = _build_diagnostics(
        artifacts,
        beat_grid=beat_grid,
        vocal_onsets=vocal_onsets,
        result=result,
        pitch_timeline=pitch_timeline,
        pitch_timeline_exists=pitch_timeline.exists(),
        warnings=warnings,
        beat_reused=beat_reused,
        onset_reused=onset_reused,
    )
    write_rhythm_diagnostics_json(diagnostics, artifacts.rhythm_diagnostics_json)
    result.diagnostics = diagnostics
    return result


def write_rhythm_diagnostics_json(diagnostics: RhythmDiagnostics, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diagnostics.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _ensure_beat_grid(
    artifacts: JobArtifacts,
    *,
    meter_hint: str,
    force: bool,
) -> tuple[BeatGridResult, bool]:
    output = artifacts.rhythm_beat_grid_json
    if output.exists() and not force:
        try:
            return BeatGridResult.model_validate_json(output.read_text(encoding="utf-8")), True
        except Exception:
            pass

    try:
        result = analyze_beat_grid(
            artifacts.accompaniment_wav,
            meter_hint=meter_hint,
            fallback_source=artifacts.analysis_audio,
        )
    except Exception as exc:
        result = _empty_beat_grid(
            artifacts.accompaniment_wav,
            [f"beat_grid_failed:{type(exc).__name__}"],
        )

    result = _normalize_beat_grid_bpm(result)
    _write_beat_grid_json(result, output)
    return result, False


def _ensure_vocal_onsets(
    artifacts: JobArtifacts,
    *,
    force: bool,
) -> tuple[list[VocalOnsetEvent], bool, list[str]]:
    output = artifacts.rhythm_vocal_onsets_csv
    if output.exists() and not force:
        return _read_vocal_onsets_csv(output), True, []

    try:
        onsets = analyze_vocal_onsets(
            artifacts.vocals_wav,
            fallback_source=artifacts.analysis_audio,
        )
        warnings: list[str] = []
    except Exception as exc:
        onsets = []
        warnings = [f"vocal_onset_failed:{type(exc).__name__}"]

    write_vocal_onsets_csv(onsets, output)
    return onsets, False, warnings


def _ensure_note_draft(
    artifacts: JobArtifacts,
    *,
    pitch_timeline: Path,
    force: bool,
    warnings: list[str],
) -> NoteDraftResult:
    output = artifacts.rhythm_notes_draft_json
    if output.exists() and artifacts.rhythm_notes_draft_csv.exists() and not force:
        try:
            return NoteDraftResult.model_validate_json(output.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        result = build_note_draft(
            pitch_timeline,
            artifacts.rhythm_beat_grid_json,
            artifacts.rhythm_vocal_onsets_csv,
        )
    except Exception as exc:
        diagnostics = RhythmDiagnostics(
            algorithm_version=NOTE_DRAFT_ALGORITHM_VERSION,
            warnings=[*warnings, f"note_draft_failed:{type(exc).__name__}"],
            beat_grid_path=_relative_artifact_path(artifacts, artifacts.rhythm_beat_grid_json),
            vocal_onsets_path=_relative_artifact_path(artifacts, artifacts.rhythm_vocal_onsets_csv),
            notes_draft_path=_relative_artifact_path(artifacts, artifacts.rhythm_notes_draft_json),
        )
        result = NoteDraftResult(
            algorithm_version=NOTE_DRAFT_ALGORITHM_VERSION,
            pitch_source="unknown",
            beat_grid_source=str(artifacts.rhythm_beat_grid_json),
            onset_source=str(artifacts.rhythm_vocal_onsets_csv),
            source_pitch_path=str(pitch_timeline),
            beat_grid_path=str(artifacts.rhythm_beat_grid_json),
            vocal_onsets_path=str(artifacts.rhythm_vocal_onsets_csv),
            notes=[],
            diagnostics=diagnostics,
            warnings=diagnostics.warnings,
        )

    result.warnings = [*warnings, *(warning for warning in result.warnings if warning not in warnings)]
    write_note_draft_json(result, output)
    write_note_draft_csv(result, artifacts.rhythm_notes_draft_csv)
    return result


def _resolve_pitch_timeline(artifacts: JobArtifacts) -> Path | None:
    direct_candidates = [
        artifacts.melody_fusion_csv,
        artifacts.melody_fusion_json,
    ]
    relative_candidates = [
        artifacts.analysis_dir / path
        for path, _label in PITCH_TIMELINE_CANDIDATES
        if path not in {"melody_fusion_csv", "melody_fusion_json"}
    ]
    return next((path for path in [*direct_candidates, *relative_candidates] if path.exists()), None)


def _build_diagnostics(
    artifacts: JobArtifacts,
    *,
    beat_grid: BeatGridResult,
    vocal_onsets: list[VocalOnsetEvent],
    result: NoteDraftResult,
    pitch_timeline: Path,
    pitch_timeline_exists: bool,
    warnings: list[str],
    beat_reused: bool,
    onset_reused: bool,
) -> RhythmDiagnostics:
    used_accompaniment = Path(beat_grid.source_audio_path) == artifacts.accompaniment_wav
    used_vocals = any(Path(onset.source_audio_path) == artifacts.vocals_wav for onset in vocal_onsets)
    used_fallback_audio = (
        Path(beat_grid.source_audio_path) == artifacts.analysis_audio
        or any(Path(onset.source_audio_path) == artifacts.analysis_audio for onset in vocal_onsets)
        or "beat_grid:used_fallback_source" in warnings
        or "vocal_onset:used_fallback_source" in warnings
    )
    all_warnings = _unique([*warnings, *(result.diagnostics.warnings if result.diagnostics else [])])
    return RhythmDiagnostics(
        algorithm_version=ALGORITHM_VERSION,
        beat_backend=beat_grid.backend,
        onset_backend=_onset_backend(vocal_onsets),
        pitch_source=result.pitch_source if pitch_timeline_exists else None,
        meter_hypotheses=beat_grid.meter_hypotheses,
        note_stats={
            "beat_count": len(beat_grid.beats) or len(beat_grid.beat_times_sec),
            "onset_count": len(vocal_onsets),
            "note_count": len(result.notes),
            "voiced_note_count": len(result.notes),
            "missing_pitch_timeline": not pitch_timeline_exists,
            "used_accompaniment": used_accompaniment,
            "used_vocals": used_vocals,
            "used_fallback_audio": used_fallback_audio,
            "beat_grid_reused": beat_reused,
            "vocal_onsets_reused": onset_reused,
        },
        warnings=all_warnings,
        beat_grid_path=_relative_artifact_path(artifacts, artifacts.rhythm_beat_grid_json),
        vocal_onsets_path=_relative_artifact_path(artifacts, artifacts.rhythm_vocal_onsets_csv),
        notes_draft_path=_relative_artifact_path(artifacts, artifacts.rhythm_notes_draft_json),
    )


def _empty_beat_grid(source: Path, warnings: list[str]) -> BeatGridResult:
    return BeatGridResult(
        algorithm_version=ALGORITHM_VERSION,
        source_audio_path=str(source),
        duration_seconds=0,
        bpm=None,
        meter="none",
        meter_used="none",
        beats_per_bar=None,
        beat_times_sec=[],
        bar_starts_sec=[],
        beats=[],
        warnings=warnings,
    )


def _write_beat_grid_json(result: BeatGridResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _normalize_beat_grid_bpm(result: BeatGridResult) -> BeatGridResult:
    bpm = _as_bpm(result.bpm)
    return result.model_copy(
        update={
            "bpm": bpm,
            "beats": [
                beat.model_copy(update={"tempo_bpm": _as_bpm(beat.tempo_bpm)})
                for beat in result.beats
            ],
        }
    )


def _as_bpm(value) -> int | None:
    if value is None:
        return None
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    if bpm <= 0:
        return None
    return max(1, int(round(bpm)))


def _read_vocal_onsets_csv(path: Path) -> list[VocalOnsetEvent]:
    onsets: list[VocalOnsetEvent] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                time_sec = _optional_float(row.get("time_sec"))
                raw_score = _optional_float(row.get("raw_score")) or 0.0
                if time_sec is None:
                    continue
                onsets.append(
                    VocalOnsetEvent(
                        onset_id=row.get("onset_id") or f"onset-{index + 1:04d}",
                        time_sec=time_sec,
                        confidence=raw_score,
                        raw_score=raw_score,
                        backtracked_time_sec=_optional_float(row.get("backtracked_time_sec")),
                        source_backend=row.get("source_backend") or "librosa",
                        is_primary=_optional_bool(row.get("is_primary"), default=True),
                    )
                )
    except Exception:
        return []
    return onsets


def _collect_onset_warnings(onsets: list[VocalOnsetEvent]) -> list[str]:
    return [warning for onset in onsets for warning in onset.warnings]


def _prefixed_warnings(prefix: str, warnings: list[str]) -> list[str]:
    return [f"{prefix}:{warning}" for warning in warnings]


def _onset_backend(onsets: list[VocalOnsetEvent]) -> str | None:
    return onsets[0].source_backend if onsets else "librosa"


def _relative_artifact_path(artifacts: JobArtifacts, path: Path) -> str:
    try:
        return path.relative_to(artifacts.root).as_posix()
    except ValueError:
        return str(path)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
