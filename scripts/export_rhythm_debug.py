#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.rhythm import BeatGridResult, NoteDraftResult  # noqa: E402
from app.services.artifacts import JobArtifacts  # noqa: E402

BEAT_PREVIEW_FIELDS = ["index", "time_sec", "bar_index", "beat_in_bar", "confidence"]
ONSET_PREVIEW_FIELDS = [
    "onset_id",
    "time_sec",
    "raw_score",
    "backtracked_time_sec",
    "source_backend",
    "is_primary",
]
NOTE_PREVIEW_FIELDS = [
    "note_id",
    "start_sec",
    "end_sec",
    "duration_sec",
    "midi_note",
    "note_name",
    "frequency_hz",
    "raw_beat_start",
    "raw_beat_duration",
    "quantized_beat_start",
    "quantized_beat_duration",
    "bar_index",
    "pitch_confidence",
    "onset_confidence",
    "quantization_confidence",
    "boundary_source",
    "warnings",
]


def export_rhythm_debug(job_dir: Path, output_dir: Path) -> dict[str, Any]:
    artifacts = JobArtifacts(job_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "beat_grid": artifacts.rhythm_beat_grid_json,
        "vocal_onsets": artifacts.rhythm_vocal_onsets_csv,
        "notes_draft_json": artifacts.rhythm_notes_draft_json,
        "notes_draft_csv": artifacts.rhythm_notes_draft_csv,
        "rhythm_diagnostics": artifacts.rhythm_diagnostics_json,
    }
    artifact_exists = {name: path.exists() for name, path in paths.items()}
    missing_artifacts = [name for name, exists in artifact_exists.items() if not exists]

    beat_grid, beat_warnings = _load_beat_grid(paths["beat_grid"])
    onsets, onset_warnings = _read_csv(paths["vocal_onsets"])
    notes_result, note_rows, note_warnings = _load_notes(paths["notes_draft_json"], paths["notes_draft_csv"])
    diagnostics, diagnostics_read_warnings = _read_json(paths["rhythm_diagnostics"])

    beat_rows = _beat_preview_rows(beat_grid)
    onset_rows = _onset_preview_rows(onsets)
    note_preview_rows = _note_preview_rows(notes_result, note_rows)

    _write_csv(output_dir / "beat_grid_preview.csv", BEAT_PREVIEW_FIELDS, beat_rows)
    _write_csv(output_dir / "vocal_onsets_preview.csv", ONSET_PREVIEW_FIELDS, onset_rows)
    _write_csv(output_dir / "notes_draft_preview.csv", NOTE_PREVIEW_FIELDS, note_preview_rows)

    diagnostics_warnings = _as_list(diagnostics.get("warnings"))
    warnings = _unique(
        [
            *beat_warnings,
            *onset_warnings,
            *note_warnings,
            *diagnostics_read_warnings,
            *(_as_list(beat_grid.get("warnings")) if beat_grid else []),
            *(_as_list(notes_result.get("warnings")) if notes_result else []),
        ]
    )

    summary = {
        "job_dir": str(job_dir),
        "artifact_exists": artifact_exists,
        "missing_artifacts": missing_artifacts,
        "beat_count": len(beat_rows),
        "onset_count": len(onset_rows),
        "note_count": len(note_preview_rows),
        "bpm": beat_grid.get("bpm") if beat_grid else None,
        "meter_used": _meter_used(beat_grid, notes_result),
        "pitch_source": _pitch_source(notes_result, diagnostics),
        "warnings": warnings,
        "diagnostics_warnings": diagnostics_warnings,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _write_json(output_dir / "rhythm_summary.json", summary)

    report = _quality_report(
        beat_grid=beat_grid,
        beat_rows=beat_rows,
        onsets=onset_rows,
        notes=note_preview_rows,
        diagnostics=diagnostics,
        summary=summary,
    )
    (output_dir / "rhythm_quality_report.txt").write_text(report, encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export CSV/TXT summaries for rhythm artifacts.")
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = export_rhythm_debug(args.job_dir, args.out)
    print(f"rhythm debug exported: {args.out}")
    if summary["missing_artifacts"]:
        print("missing artifacts:", ", ".join(summary["missing_artifacts"]))


def _load_beat_grid(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, ["missing_artifact:beat_grid"]
    try:
        result = BeatGridResult.model_validate_json(path.read_text(encoding="utf-8"))
        return result.model_dump(), []
    except Exception:
        payload, warnings = _read_json(path)
        if payload:
            return payload, warnings
        return {}, [*warnings, "invalid_artifact:beat_grid"]


def _load_notes(
    json_path: Path,
    csv_path: Path,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    result: dict[str, Any] = {}
    if json_path.exists():
        try:
            model = NoteDraftResult.model_validate_json(json_path.read_text(encoding="utf-8"))
            result = model.model_dump()
        except Exception:
            result, json_warnings = _read_json(json_path)
            warnings.extend(json_warnings)
            if not result:
                warnings.append("invalid_artifact:notes_draft_json")
    else:
        warnings.append("missing_artifact:notes_draft_json")

    rows, csv_warnings = _read_csv(csv_path)
    warnings.extend(csv_warnings)
    return result, rows, warnings


def _read_json(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, [f"missing_artifact:{path.name}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"json_read_failed:{path.name}:{type(exc).__name__}"]
    return payload if isinstance(payload, dict) else {}, []


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        return [], [f"missing_artifact:{path.name}"]
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle)), []
    except Exception as exc:
        return [], [f"csv_read_failed:{path.name}:{type(exc).__name__}"]


def _beat_preview_rows(beat_grid: dict[str, Any]) -> list[dict[str, Any]]:
    beats = beat_grid.get("beats")
    if isinstance(beats, list) and beats:
        return [
            {
                "index": beat.get("beat_index", index),
                "time_sec": beat.get("time_sec", ""),
                "bar_index": beat.get("bar_index", ""),
                "beat_in_bar": beat.get("beat_in_bar", ""),
                "confidence": beat.get("confidence", ""),
            }
            for index, beat in enumerate(beats)
            if isinstance(beat, dict)
        ]

    beat_times = beat_grid.get("beat_times_sec")
    if not isinstance(beat_times, list):
        return []
    return [
        {"index": index, "time_sec": time_sec, "bar_index": "", "beat_in_bar": "", "confidence": ""}
        for index, time_sec in enumerate(beat_times)
    ]


def _onset_preview_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [{field: row.get(field, "") for field in ONSET_PREVIEW_FIELDS} for row in rows]


def _note_preview_rows(
    notes_result: dict[str, Any],
    csv_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    notes = notes_result.get("notes")
    if isinstance(notes, list):
        return [
            {field: _format_note_value(note.get(field)) for field in NOTE_PREVIEW_FIELDS}
            for note in notes
            if isinstance(note, dict)
        ]
    return [{field: row.get(field, "") for field in NOTE_PREVIEW_FIELDS} for row in csv_rows]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _quality_report(
    *,
    beat_grid: dict[str, Any],
    beat_rows: list[dict[str, Any]],
    onsets: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    durations = [_float(note.get("duration_sec")) for note in notes]
    durations = [value for value in durations if value is not None]
    low_quantization = [
        note
        for note in notes
        if (confidence := _float(note.get("quantization_confidence"))) is not None
        and confidence < 0.5
    ]
    without_beat = [note for note in notes if note.get("raw_beat_start") in {"", None}]
    first_onset = _float(onsets[0].get("time_sec")) if onsets else None
    last_onset = _float(onsets[-1].get("time_sec")) if onsets else None

    lines = [
        "Rhythm Quality Report",
        f"Generated At: {summary['generated_at']}",
        f"Job Dir: {summary['job_dir']}",
        "",
        "1. Beat Grid Summary",
        f"- bpm: {beat_grid.get('bpm') if beat_grid else None}",
        f"- meter_used: {summary.get('meter_used')}",
        f"- beat_count: {len(beat_rows)}",
        f"- bar_count: {_bar_count(beat_grid, beat_rows)}",
        f"- warnings: {_joined(_as_list(beat_grid.get('warnings')) if beat_grid else [])}",
        "",
        "2. Vocal Onset Summary",
        f"- onset_count: {len(onsets)}",
        f"- first_onset_sec: {_format_optional(first_onset)}",
        f"- last_onset_sec: {_format_optional(last_onset)}",
        f"- warnings: {_joined(_onset_warnings(onsets))}",
        "",
        "3. Note Draft Summary",
        f"- note_count: {len(notes)}",
        f"- average_note_duration_sec: {_format_optional(mean(durations) if durations else None)}",
        f"- shortest_note_duration_sec: {_format_optional(min(durations) if durations else None)}",
        f"- longest_note_duration_sec: {_format_optional(max(durations) if durations else None)}",
        f"- notes_with_low_quantization_confidence: {len(low_quantization)}",
        f"- notes_without_beat_position: {len(without_beat)}",
        f"- warnings: {_joined(summary.get('warnings', []))}",
        "",
        "4. Diagnostics",
        f"- pitch_source: {summary.get('pitch_source')}",
        f"- missing artifacts: {_joined(summary.get('missing_artifacts', []))}",
        f"- fallback usage: {_fallback_usage(diagnostics)}",
        "- known limitations: beat grid is an initial implementation; auto meter may not infer bars; "
        "6/8 uses a conservative pulse assumption; vocal onsets are candidate cut points; "
        "notes_draft is not final numbered notation, MusicXML, or PDF.",
    ]
    return "\n".join(lines) + "\n"


def _meter_used(beat_grid: dict[str, Any], notes_result: dict[str, Any]) -> Any:
    return (
        beat_grid.get("meter_used")
        or beat_grid.get("meter")
        or notes_result.get("meter_used")
    )


def _pitch_source(notes_result: dict[str, Any], diagnostics: dict[str, Any]) -> Any:
    return notes_result.get("pitch_source") or diagnostics.get("pitch_source")


def _bar_count(beat_grid: dict[str, Any], beat_rows: list[dict[str, Any]]) -> int:
    bar_starts = beat_grid.get("bar_starts_sec")
    if isinstance(bar_starts, list) and bar_starts:
        return len(bar_starts)
    bar_indexes = {_optional_int(row.get("bar_index")) for row in beat_rows}
    return len({index for index in bar_indexes if index is not None})


def _fallback_usage(diagnostics: dict[str, Any]) -> str:
    note_stats = diagnostics.get("note_stats")
    if isinstance(note_stats, dict) and note_stats.get("used_fallback_audio") is not None:
        return str(bool(note_stats.get("used_fallback_audio")))
    warnings = _as_list(diagnostics.get("warnings"))
    return "true" if any("fallback" in warning for warning in warnings) else "unknown"


def _onset_warnings(onsets: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for onset in onsets:
        value = onset.get("warnings")
        warnings.extend(_as_list(value))
    return _unique(warnings)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        if not value:
            return []
        return [part for part in value.split(";") if part]
    return [str(value)]


def _format_note_value(value: Any) -> Any:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if value is None:
        return ""
    return value


def _float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    parsed = _float(value)
    return int(parsed) if parsed is not None else None


def _format_optional(value: float | None) -> str:
    return "None" if value is None else f"{value:.6f}"


def _joined(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


if __name__ == "__main__":
    main()
