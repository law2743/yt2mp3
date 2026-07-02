from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models.rhythm import BeatEvent, BeatGridResult, VocalOnsetEvent
from app.services.artifacts import JobArtifacts
from app.services.rhythm.pipeline import run_rhythm_pipeline


def _artifacts(tmp_path: Path) -> JobArtifacts:
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.analysis_audio.write_bytes(b"mono")
    return artifacts


def _write_pitch_csv(path: Path, midis: list[float] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = midis or [69.0] * 12
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["time_sec", "midi", "f0_hz", "confidence", "voiced"],
        )
        writer.writeheader()
        for index, midi in enumerate(values):
            writer.writerow(
                {
                    "time_sec": round(index * 0.02, 6),
                    "midi": midi,
                    "f0_hz": 440.0,
                    "confidence": 0.9,
                    "voiced": 1,
                }
            )


def _write_postprocessed_csv(path: Path, midis: list[float] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = midis or [72.0] * 12
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "time_sec",
                "hybrid_postprocessed_f0_hz",
                "hybrid_postprocessed_midi",
                "fusion_postprocessed_f0_hz",
                "fusion_postprocessed_midi",
                "rmvpe_postprocessed_f0_hz",
                "rmvpe_postprocessed_midi",
                "hybrid_postprocess_action",
                "hybrid_support_count",
                "hybrid_supporters",
                "voiced",
            ],
        )
        writer.writeheader()
        for index, midi in enumerate(values):
            hz = 440.0 * (2.0 ** ((midi - 69.0) / 12.0))
            writer.writerow(
                {
                    "time_sec": round(index * 0.02, 6),
                    "hybrid_postprocessed_f0_hz": hz,
                    "hybrid_postprocessed_midi": midi,
                    "fusion_postprocessed_f0_hz": hz,
                    "fusion_postprocessed_midi": midi,
                    "rmvpe_postprocessed_f0_hz": hz,
                    "rmvpe_postprocessed_midi": midi,
                    "hybrid_postprocess_action": "rmvpe_primary",
                    "hybrid_support_count": "",
                    "hybrid_supporters": "",
                    "voiced": 1,
                }
            )


def _fake_beat_grid(source: Path, *, fallback_source: Path | None = None, **_kwargs) -> BeatGridResult:
    selected = fallback_source if not source.exists() and fallback_source and fallback_source.exists() else source
    warnings = ["source_missing", "used_fallback_source"] if selected != source else []
    return BeatGridResult(
        algorithm_version="test-beat",
        source_audio_path=str(selected),
        duration_seconds=2.0,
        bpm=120.0,
        meter="4/4",
        meter_used="4/4",
        beats_per_bar=4,
        beat_times_sec=[0.0, 0.5, 1.0, 1.5],
        beats=[
            BeatEvent(beat_index=index, time_sec=time_sec, tempo_bpm=120.0)
            for index, time_sec in enumerate([0.0, 0.5, 1.0, 1.5])
        ],
        warnings=warnings,
    )


def _fake_onsets(source: Path, *, fallback_source: Path | None = None, **_kwargs) -> list[VocalOnsetEvent]:
    selected = fallback_source if not source.exists() and fallback_source and fallback_source.exists() else source
    warnings = ["source_missing", "used_fallback_source"] if selected != source else []
    return [
        VocalOnsetEvent(
            onset_id="onset-0001",
            time_sec=0.08,
            confidence=0.8,
            raw_score=0.8,
            source_audio_path=str(selected),
            warnings=warnings,
        )
    ]


def _patch_services(monkeypatch) -> None:
    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_beat_grid", _fake_beat_grid)
    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_vocal_onsets", _fake_onsets)


def test_full_rhythm_pipeline_writes_all_artifacts(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes
    assert artifacts.rhythm_beat_grid_json.exists()
    assert artifacts.rhythm_vocal_onsets_csv.exists()
    assert artifacts.rhythm_notes_draft_json.exists()
    assert artifacts.rhythm_notes_draft_csv.exists()
    assert artifacts.rhythm_diagnostics_json.exists()
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert diagnostics["note_stats"]["beat_count"] == 4
    assert diagnostics["note_stats"]["onset_count"] == 1
    assert diagnostics["note_stats"]["used_accompaniment"] is True
    assert diagnostics["note_stats"]["used_vocals"] is True


def test_pipeline_prefers_postprocessed_hybrid_over_raw_fusion(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_postprocessed_csv(artifacts.melody_postprocessed_csv, [72.0] * 12)
    _write_pitch_csv(artifacts.melody_fusion_csv, [69.0] * 12)
    _patch_services(monkeypatch)

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.pitch_source == "hybrid_postprocessed"
    assert result.notes[0].midi_note == 72
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert diagnostics["pitch_source"] == "hybrid_postprocessed"
    assert diagnostics["note_stats"]["postprocessed_artifact_used"] is True
    assert diagnostics["note_stats"]["fallback_to_raw_fusion"] is False


def test_pipeline_falls_back_to_raw_fusion_when_postprocessed_missing(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv, [69.0] * 12)
    _patch_services(monkeypatch)

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert diagnostics["note_stats"]["postprocessed_artifact_used"] is False
    assert diagnostics["note_stats"]["fallback_to_raw_fusion"] is True


def test_missing_accompaniment_falls_back_to_mono_audio(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    beat_grid = json.loads(artifacts.rhythm_beat_grid_json.read_text(encoding="utf-8"))
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert beat_grid["source_audio_path"] == str(artifacts.analysis_audio)
    assert diagnostics["note_stats"]["used_fallback_audio"] is True


def test_missing_vocals_falls_back_to_mono_audio(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert diagnostics["note_stats"]["used_fallback_audio"] is True
    assert "vocal_onset:used_fallback_source" in diagnostics["warnings"]


def test_missing_pitch_timeline_keeps_partial_artifacts_and_empty_notes(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _patch_services(monkeypatch)

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes == []
    assert artifacts.rhythm_beat_grid_json.exists()
    assert artifacts.rhythm_vocal_onsets_csv.exists()
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert diagnostics["note_stats"]["missing_pitch_timeline"] is True
    assert "missing_pitch_timeline" in diagnostics["warnings"]


def test_beat_grid_failure_does_not_crash(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    monkeypatch.setattr(
        "app.services.rhythm.pipeline.analyze_beat_grid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_vocal_onsets", _fake_onsets)

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes
    assert result.notes[0].raw_beat_start is None
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert any(warning.startswith("beat_grid:beat_grid_failed") for warning in diagnostics["warnings"])


def test_vocal_onset_failure_does_not_crash_or_block_notes(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_beat_grid", _fake_beat_grid)
    monkeypatch.setattr(
        "app.services.rhythm.pipeline.analyze_vocal_onsets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes
    diagnostics = json.loads(artifacts.rhythm_diagnostics_json.read_text(encoding="utf-8"))
    assert any(warning.startswith("vocal_onset_failed") for warning in diagnostics["warnings"])


def test_lead_selection_failure_does_not_crash_or_block_notes(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)
    monkeypatch.setattr(
        "app.services.rhythm.pipeline.run_lead_selection_diagnostics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert result.notes
    assert artifacts.rhythm_notes_draft_json.exists()
    assert artifacts.rhythm_notes_draft_csv.exists()
    diagnostics = json.loads(
        artifacts.melody_lead_selection_diagnostics_json.read_text(encoding="utf-8")
    )
    assert diagnostics["num_phrases"] == 0
    assert any(error.startswith("lead_selection_failed:RuntimeError") for error in diagnostics["errors"])


def test_force_true_regenerates_existing_artifacts(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    calls = {"beat": 0}

    def counted_beat(source: Path, **kwargs) -> BeatGridResult:
        calls["beat"] += 1
        result = _fake_beat_grid(source, **kwargs)
        result.bpm = 100.0 + calls["beat"]
        return result

    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_beat_grid", counted_beat)
    monkeypatch.setattr("app.services.rhythm.pipeline.analyze_vocal_onsets", _fake_onsets)

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4")
    run_rhythm_pipeline(artifacts.root, meter_hint="4/4", force=False)
    assert calls["beat"] == 1

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4", force=True)
    assert calls["beat"] == 2
    beat_grid = json.loads(artifacts.rhythm_beat_grid_json.read_text(encoding="utf-8"))
    assert beat_grid["bpm"] == 102.0


def test_artifact_paths_are_under_analysis_rhythm(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert artifacts.rhythm_beat_grid_json == artifacts.root / "analysis" / "rhythm" / "beat_grid.json"
    assert artifacts.rhythm_vocal_onsets_csv == artifacts.root / "analysis" / "rhythm" / "vocal_onsets.csv"
    assert artifacts.rhythm_notes_draft_json == artifacts.root / "analysis" / "rhythm" / "notes_draft.json"
    assert artifacts.rhythm_notes_draft_csv == artifacts.root / "analysis" / "rhythm" / "notes_draft.csv"
    assert artifacts.rhythm_diagnostics_json == (
        artifacts.root / "analysis" / "rhythm" / "rhythm_diagnostics.json"
    )


def test_pipeline_does_not_create_or_overwrite_melody_outputs(tmp_path, monkeypatch):
    artifacts = _artifacts(tmp_path)
    artifacts.stems_dir.mkdir()
    artifacts.accompaniment_wav.write_bytes(b"accompaniment")
    artifacts.vocals_wav.write_bytes(b"vocals")
    artifacts.melody_json.write_text("existing melody json", encoding="utf-8")
    artifacts.melody_midi.write_bytes(b"existing melody midi")
    _write_pitch_csv(artifacts.melody_fusion_csv)
    _patch_services(monkeypatch)

    run_rhythm_pipeline(artifacts.root, meter_hint="4/4")

    assert artifacts.melody_json.read_text(encoding="utf-8") == "existing melody json"
    assert artifacts.melody_midi.read_bytes() == b"existing melody midi"
