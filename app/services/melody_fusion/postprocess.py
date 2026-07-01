from __future__ import annotations

import csv
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "melody_fusion_postprocess_v1_20260701"
OFFICIAL_MODELS = ("fusion", "rmvpe", "torchcrepe", "fcpe", "pesto")
REFERENCE_MODELS = ("rmvpe", "torchcrepe", "fcpe", "pesto")
MODEL_WEIGHTS = {"rmvpe": 1.25, "torchcrepe": 1.05, "pesto": 0.90, "fcpe": 0.45}
TIME_COLUMNS = ("time_sec", "time_seconds", "time", "seconds", "sec", "timestamp", "t")
PITCH_HINTS = ("f0_hz", "pitch_hz", "frequency_hz", "freq_hz", "hz", "midi", "note_number", "f0", "pitch")


@dataclass(frozen=True)
class PostprocessConfig:
    support_cents: float = 60.0
    octave_tolerance_cents: float = 125.0
    context_window_sec: float = 0.80
    min_voiced_island_ms: float = 80.0
    min_note_ms: float = 100.0
    note_change_cents: float = 80.0
    merge_gap_ms: float = 80.0
    merge_pitch_cents: float = 100.0
    hybrid_support_cents: float = 60.0
    hybrid_min_support: int = 2
    rmvpe_strict: bool = True


@dataclass(frozen=True)
class PostprocessArtifacts:
    comparison_csv: Path
    fusion_csv: Path | None
    diagnostics_json: Path | None
    output_csv: Path
    output_json: Path
    output_diagnostics_json: Path
    backend_csvs: dict[str, Path] = field(default_factory=dict)


@dataclass
class SeriesResult:
    name: str
    original_midi: list[float | None]
    corrected_midi: list[float | None]
    actions: list[str]
    corrections: list[dict[str, Any]] = field(default_factory=list)
    removed_islands: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def postprocess_melody_fusion_artifacts(
    artifacts: PostprocessArtifacts,
    *,
    config: PostprocessConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PostprocessConfig()
    comparison_csv = artifacts.comparison_csv
    if not comparison_csv.exists():
        if artifacts.fusion_csv is None:
            raise FileNotFoundError("comparison.csv is missing and fusion.csv is unavailable")
        _write_comparison_from_artifacts(
            comparison_csv,
            fusion_csv=artifacts.fusion_csv,
            backend_csvs=artifacts.backend_csvs,
        )

    headers, rows = _read_csv(comparison_csv)
    time_col, series, column_by_model = _load_official_series(headers, rows)
    times = [_safe_float(row.get(time_col)) if _safe_float(row.get(time_col)) is not None else float(idx) for idx, row in enumerate(rows)]
    hop_sec = _estimate_hop(times)
    octave_candidates = _find_octave_candidates(times, series, cfg.octave_tolerance_cents)

    results = {
        target: _process_target(target, times, series, cfg, hop_sec)
        for target in ("rmvpe", "fusion")
    }
    hybrid_result = _build_hybrid_result(times, series, results["rmvpe"], results["fusion"], cfg, hop_sec)
    out_headers, out_rows = _create_postprocessed_rows(headers, rows, results, hybrid_result)
    _write_csv(artifacts.output_csv, out_headers, out_rows)

    payload = {
        "version": SCRIPT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "comparison_csv": str(comparison_csv),
            "fusion_csv": str(artifacts.fusion_csv) if artifacts.fusion_csv else None,
        },
        "tracks": {
            "rmvpe_postprocessed": {
                "mode": "rmvpe_strict_no_gap_fill" if cfg.rmvpe_strict else "rmvpe_non_strict",
                "stats": results["rmvpe"].stats,
                "notes": results["rmvpe"].notes,
            },
            "fusion_postprocessed": {
                "mode": "fusion_clean_no_new_voiced_frames_v1",
                "stats": results["fusion"].stats,
                "notes": results["fusion"].notes,
            },
            "hybrid_postprocessed": {
                "mode": "rmvpe_primary_fusion_gap_fill_when_supported",
                "stats": hybrid_result["stats"],
                "notes": hybrid_result["notes"],
            },
        },
    }
    _write_json(artifacts.output_json, payload)

    diagnostics = {
        "version": SCRIPT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "comparison_csv": str(comparison_csv),
            "fusion_csv": str(artifacts.fusion_csv) if artifacts.fusion_csv else None,
            "diagnostics_json": str(artifacts.diagnostics_json) if artifacts.diagnostics_json else None,
        },
        "parameters": cfg.__dict__,
        "columns": column_by_model,
        "time_column": time_col,
        "estimated_hop_sec": hop_sec,
        "octave_candidates_count": len(octave_candidates),
        "octave_candidates_top": octave_candidates[:200],
        "targets": {
            "rmvpe": {
                "stats": results["rmvpe"].stats,
                "octave_corrections": results["rmvpe"].corrections[:500],
                "removed_short_islands": results["rmvpe"].removed_islands[:500],
            },
            "fusion": {
                "stats": results["fusion"].stats,
                "octave_corrections": results["fusion"].corrections[:500],
                "removed_short_islands": results["fusion"].removed_islands[:500],
            },
            "hybrid": {
                "stats": hybrid_result["stats"],
                "fill_events": hybrid_result["fill_events"][:500],
            },
        },
        "source_diagnostics_summary": _summarize_source_diagnostics(_load_json(artifacts.diagnostics_json)),
        "warnings": [],
    }
    _write_json(artifacts.output_diagnostics_json, diagnostics)
    return {"postprocessed": payload, "diagnostics": diagnostics}


def _write_comparison_from_artifacts(
    output_csv: Path,
    *,
    fusion_csv: Path,
    backend_csvs: dict[str, Path],
) -> None:
    fusion_rows = _read_csv_dicts(fusion_csv)
    if not fusion_rows:
        raise ValueError("fusion.csv is empty")
    backend_rows = {
        backend: {row.get("time_sec", ""): row for row in _read_csv_dicts(path)}
        for backend, path in backend_csvs.items()
        if path.exists()
    }
    headers = ["time_sec", "fusion_f0_hz", "fusion_midi", *[f"{b}_f0_hz" for b in REFERENCE_MODELS]]
    rows: list[dict[str, Any]] = []
    for fusion_row in fusion_rows:
        time_sec = fusion_row.get("time_sec", "")
        out: dict[str, Any] = {
            "time_sec": time_sec,
            "fusion_f0_hz": fusion_row.get("f0_hz") or "",
            "fusion_midi": fusion_row.get("midi") or "",
        }
        for backend in REFERENCE_MODELS:
            source = backend_rows.get(backend, {}).get(time_sec, {})
            out[f"{backend}_f0_hz"] = source.get("f0_hz") or ""
        rows.append(out)
    _write_csv(output_csv, headers, rows)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if math.isfinite(n) else None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "inf", "-inf"}:
        return None
    try:
        n = float(text)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


def _hz_to_midi(hz: float | None) -> float | None:
    if hz is None or hz <= 0 or not math.isfinite(hz):
        return None
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def _midi_to_hz(midi: float | None) -> float | None:
    if midi is None or not math.isfinite(midi):
        return None
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _cents(a: float, b: float) -> float:
    return abs(a - b) * 100.0


def _median(values: list[float]) -> float | None:
    cleaned = [v for v in values if v is not None and math.isfinite(v)]
    return float(statistics.median(cleaned)) if cleaned else None


def _choose_time_column(headers: list[str]) -> str | None:
    lower_map = {h.lower().strip(): h for h in headers}
    for name in TIME_COLUMNS:
        if name in lower_map:
            return lower_map[name]
    return next((h for h in headers if "time" in h.lower() and "timeout" not in h.lower()), None)


def _looks_like_hz(column: str, values: list[float]) -> bool:
    lower = column.lower().strip()
    if "midi" in lower or "note_number" in lower:
        return False
    if any(hint in lower for hint in ("hz", "f0", "freq", "frequency")):
        return True
    return bool(values) and statistics.median(values) > 125


def _find_pitch_column(headers: list[str], model: str) -> str | None:
    model_keywords = {
        "fusion": ("fusion", "fused"),
        "rmvpe": ("rmvpe",),
        "torchcrepe": ("torchcrepe", "torch_crepe", "torch-crepe"),
        "fcpe": ("fcpe",),
        "pesto": ("pesto",),
    }.get(model, (model,))
    candidates: list[tuple[int, int, str]] = []
    for h in headers:
        lower = h.lower().strip()
        if "raw_f0" in lower or any(skip in lower for skip in ("confidence", "conf", "prob", "score", "voiced", "delta", "diff", "error", "range", "spread")):
            continue
        if not any(keyword in lower for keyword in model_keywords):
            continue
        if not any(hint in lower for hint in PITCH_HINTS):
            continue
        score = 100
        normalized = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
        for keyword in model_keywords:
            k = re.sub(r"[^a-z0-9]+", "_", keyword).strip("_")
            if normalized in {f"{k}_f0_hz", f"{k}_pitch_hz", f"{k}_midi", f"{k}_f0"}:
                score = 0
                break
        for idx, hint in enumerate(PITCH_HINTS):
            if hint in lower:
                score = min(score, idx + 10)
        candidates.append((score, len(h), h))
    return sorted(candidates)[0][2] if candidates else None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return _read_csv(path)[1]


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_json(path: Path | None) -> Any | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _load_official_series(headers: list[str], rows: list[dict[str, str]]) -> tuple[str, dict[str, list[float | None]], dict[str, str]]:
    time_col = _choose_time_column(headers)
    if time_col is None:
        raise ValueError("comparison.csv does not contain a recognizable time column")
    column_by_model = {model: col for model in OFFICIAL_MODELS if (col := _find_pitch_column(headers, model))}
    missing_required = sorted({"fusion", "rmvpe"} - set(column_by_model))
    if missing_required:
        raise ValueError(f"comparison.csv missing required pitch columns for: {', '.join(missing_required)}")
    series: dict[str, list[float | None]] = {}
    for model, col in column_by_model.items():
        parsed = [_safe_float(row.get(col)) for row in rows]
        raw_values = [value for value in parsed if value is not None and value > 0]
        convert_hz = _looks_like_hz(col, raw_values)
        values: list[float | None] = []
        for value in parsed:
            if value is None or value <= 0:
                values.append(None)
                continue
            midi = _hz_to_midi(value) if convert_hz else value
            values.append(midi if midi is not None and 20 <= midi <= 110 else None)
        series[model] = values
    return time_col, series, column_by_model


def _estimate_hop(times: list[float]) -> float:
    diffs = [times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] > times[i]]
    return float(statistics.median(diffs)) if diffs else 0.01


def _find_octave_candidates(times: list[float], series: dict[str, list[float | None]], octave_tolerance_cents: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    targets = (1200.0, 2400.0, 3600.0)
    for idx, t in enumerate(times):
        values = [(model, y) for model in OFFICIAL_MODELS if (values_for_model := series.get(model)) and (y := values_for_model[idx]) is not None]
        for i, (a_name, a_midi) in enumerate(values):
            for b_name, b_midi in values[i + 1:]:
                diff = _cents(a_midi, b_midi)
                target = min(targets, key=lambda x: abs(diff - x))
                error = abs(diff - target)
                if error <= octave_tolerance_cents:
                    rows.append({"time_sec": round(t, 4), "model_a": a_name, "model_b": b_name, "diff_cents": round(diff, 2), "octave_error_cents": round(error, 2)})
    return sorted(rows, key=lambda r: (float(r["octave_error_cents"]), -float(r["diff_cents"])))


def _local_context_median(idx: int, times: list[float], values: list[float | None], processed_prefix: list[float | None], window_sec: float) -> float | None:
    context: list[float] = []
    t = times[idx]
    j = idx - 1
    while j >= 0 and t - times[j] <= window_sec:
        y = processed_prefix[j] if j < len(processed_prefix) else values[j]
        if y is not None:
            context.append(y)
        j -= 1
    j = idx + 1
    while j < len(values) and times[j] - t <= window_sec:
        if values[j] is not None:
            context.append(values[j])
        j += 1
    return _median(context)


def _support_score(candidate_midi: float, idx: int, target_name: str, series: dict[str, list[float | None]], support_cents: float) -> tuple[float, int, list[str]]:
    score = 0.0
    count = 0
    names: list[str] = []
    for model in REFERENCE_MODELS:
        if model == target_name:
            continue
        values = series.get(model)
        if not values or values[idx] is None:
            continue
        distance = _cents(candidate_midi, values[idx])
        weight = MODEL_WEIGHTS.get(model, 0.7)
        if distance <= support_cents:
            score += weight
            count += 1
            names.append(model)
        elif distance <= support_cents * 2:
            score += 0.45 * weight
            names.append(f"{model}?")
    return score, count, names


def _correction_score(candidate_midi: float, shift: int, original_midi: float, idx: int, target_name: str, times: list[float], target_values: list[float | None], processed_prefix: list[float | None], series: dict[str, list[float | None]], support_cents: float, context_window_sec: float) -> tuple[float, dict[str, Any]]:
    score = 0.0
    support, support_count, supporters = _support_score(candidate_midi, idx, target_name, series, support_cents)
    score += support
    details: dict[str, Any] = {"support_score": round(support, 4), "support_count": support_count, "supporters": supporters}
    context = _local_context_median(idx, times, target_values, processed_prefix, context_window_sec)
    details["context_midi"] = round(context, 4) if context is not None else None
    if context is not None:
        dist = _cents(candidate_midi, context)
        details["context_distance_cents"] = round(dist, 2)
        if dist <= support_cents:
            score += 1.10
        elif dist <= support_cents * 2:
            score += 0.55
        elif dist <= 300:
            score += 0.10
        else:
            score -= min(1.5, (dist - 300) / 600)
    score += 0.35 if shift == 0 else (-0.15 if abs(shift) == 12 else -0.45)
    details.update({"shift": shift, "candidate_midi": round(candidate_midi, 4), "original_midi": round(original_midi, 4), "score": round(score, 4)})
    return score, details


def _correct_octaves_for_target(name: str, times: list[float], target_values: list[float | None], series: dict[str, list[float | None]], cfg: PostprocessConfig) -> tuple[list[float | None], list[str], list[dict[str, Any]]]:
    corrected: list[float | None] = []
    actions: list[str] = []
    corrections: list[dict[str, Any]] = []
    min_improvement = 1.20 if name == "rmvpe" and cfg.rmvpe_strict else 0.60
    min_support_for_change = 2 if name == "rmvpe" and cfg.rmvpe_strict else 1
    for idx, original in enumerate(target_values):
        if original is None:
            corrected.append(None)
            actions.append("empty")
            continue
        scored = [
            (*_correction_score(original + shift, shift, original, idx, name, times, target_values, corrected, series, cfg.support_cents, cfg.context_window_sec),)
            for shift in (-24, -12, 0, 12, 24)
        ]
        normalized = [(score, details["shift"], details) for score, details in scored]
        normalized.sort(key=lambda item: item[0], reverse=True)
        best_score, best_shift, best_details = normalized[0]
        original_score = next(score for score, shift, _ in normalized if shift == 0)
        improvement = best_score - original_score
        if best_shift != 0 and improvement >= min_improvement and best_details.get("support_count", 0) >= min_support_for_change:
            new_value = original + best_shift
            corrected.append(new_value)
            actions.append(f"octave_shift_{best_shift:+d}")
            corrections.append({"time_sec": round(times[idx], 4), "target": name, "old_midi": round(original, 4), "new_midi": round(new_value, 4), "improvement": round(improvement, 4), **best_details})
        else:
            corrected.append(original)
            actions.append("kept")
    return corrected, actions, corrections


def _remove_short_voiced_islands(name: str, times: list[float], values: list[float | None], actions: list[str], min_island_sec: float, hop_sec: float) -> tuple[list[float | None], list[str], list[dict[str, Any]]]:
    cleaned = list(values)
    new_actions = list(actions)
    removed: list[dict[str, Any]] = []
    start: int | None = None
    for idx, value in enumerate([*values, None]):
        voiced = value is not None
        if voiced and start is None:
            start = idx
        if (not voiced or idx == len(values)) and start is not None:
            end = idx - 1
            duration = max(0.0, times[end] - times[start] + hop_sec)
            if duration < min_island_sec:
                segment_values = [v for v in values[start:end + 1] if v is not None]
                for j in range(start, end + 1):
                    cleaned[j] = None
                    new_actions[j] = "removed_short_island"
                removed.append({"target": name, "start_sec": round(times[start], 4), "end_sec": round(times[end], 4), "duration_sec": round(duration, 4), "frames": end - start + 1, "median_midi": round(statistics.median(segment_values), 4) if segment_values else None})
            start = None
    return cleaned, new_actions, removed


def _build_notes(name: str, times: list[float], values: list[float | None], hop_sec: float, note_change_cents: float, min_note_sec: float, merge_gap_sec: float, merge_pitch_cents: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_notes: list[dict[str, Any]] = []
    start_idx: int | None = None
    segment_values: list[float] = []
    last_midi: float | None = None

    def flush(end_idx: int) -> None:
        nonlocal start_idx, segment_values, last_midi
        if start_idx is None or not segment_values:
            start_idx = None
            segment_values = []
            last_midi = None
            return
        med = float(statistics.median(segment_values))
        start = times[start_idx]
        end = times[end_idx] + hop_sec
        raw_notes.append({"start_sec": round(start, 4), "end_sec": round(end, 4), "duration_sec": round(max(0.0, end - start), 4), "midi": round(med, 4), "f0_hz": round(_midi_to_hz(med) or 0.0, 4), "frames": len(segment_values)})
        start_idx = None
        segment_values = []
        last_midi = None

    for idx, midi in enumerate(values):
        if midi is None:
            if start_idx is not None:
                flush(idx - 1)
            continue
        if start_idx is None:
            start_idx = idx
            segment_values = [midi]
            last_midi = midi
            continue
        assert last_midi is not None
        if _cents(midi, last_midi) > note_change_cents:
            flush(idx - 1)
            start_idx = idx
            segment_values = [midi]
        else:
            segment_values.append(midi)
        last_midi = midi
    if start_idx is not None:
        flush(len(values) - 1)

    removed_short_notes = 0
    merged: list[dict[str, Any]] = []
    for note in raw_notes:
        if note["duration_sec"] < min_note_sec:
            if merged:
                prev = merged[-1]
                gap = float(note["start_sec"]) - float(prev["end_sec"])
                pitch_dist = _cents(float(note["midi"]), float(prev["midi"]))
                if gap <= merge_gap_sec and pitch_dist <= merge_pitch_cents:
                    prev["end_sec"] = note["end_sec"]
                    prev["duration_sec"] = round(float(prev["end_sec"]) - float(prev["start_sec"]), 4)
                    prev["frames"] = int(prev.get("frames", 0)) + int(note.get("frames", 0))
                    prev["merged_short_note"] = True
                    removed_short_notes += 1
                    continue
            removed_short_notes += 1
            continue
        merged.append(note)
    return merged, {"target": name, "raw_note_count": len(raw_notes), "cleaned_note_count": len(merged), "removed_or_merged_short_notes": removed_short_notes, "min_note_sec": min_note_sec, "note_change_cents": note_change_cents}


def _process_target(name: str, times: list[float], series: dict[str, list[float | None]], cfg: PostprocessConfig, hop_sec: float) -> SeriesResult:
    original = series[name]
    corrected, actions, corrections = _correct_octaves_for_target(name, times, original, series, cfg)
    cleaned, actions, removed_islands = _remove_short_voiced_islands(name, times, corrected, actions, cfg.min_voiced_island_ms / 1000.0, hop_sec)
    notes, note_stats = _build_notes(name, times, cleaned, hop_sec, cfg.note_change_cents, cfg.min_note_ms / 1000.0, cfg.merge_gap_ms / 1000.0, cfg.merge_pitch_cents)
    original_voiced = sum(1 for v in original if v is not None)
    cleaned_voiced = sum(1 for v in cleaned if v is not None)
    stats = {"target": name, "frames": len(original), "original_voiced_frames": original_voiced, "postprocessed_voiced_frames": cleaned_voiced, "original_voiced_ratio": round(original_voiced / len(original), 6) if original else 0, "postprocessed_voiced_ratio": round(cleaned_voiced / len(cleaned), 6) if cleaned else 0, "octave_correction_count": len(corrections), "removed_short_island_count": len(removed_islands), **note_stats}
    return SeriesResult(name, original, cleaned, actions, corrections, removed_islands, notes, stats)


def _hybrid_support_for_fusion_gap(idx: int, fusion_midi: float, series: dict[str, list[float | None]], support_cents: float) -> tuple[int, list[str]]:
    supporters: list[str] = []
    for model in ("torchcrepe", "fcpe", "pesto"):
        values = series.get(model)
        if values and idx < len(values) and values[idx] is not None and _cents(fusion_midi, values[idx]) <= support_cents:
            supporters.append(model)
    return len(supporters), supporters


def _build_hybrid_result(times: list[float], series: dict[str, list[float | None]], rmvpe_result: SeriesResult, fusion_result: SeriesResult, cfg: PostprocessConfig, hop_sec: float) -> dict[str, Any]:
    hybrid_midi: list[float | None] = []
    actions: list[str] = []
    support_counts: list[int | None] = []
    supporters_by_frame: list[list[str]] = []
    fill_events: list[dict[str, Any]] = []
    rmvpe_primary_frames = fusion_gap_fill_frames = fusion_gap_rejected_frames = 0
    for idx, rmvpe_midi in enumerate(rmvpe_result.corrected_midi):
        fusion_midi = fusion_result.corrected_midi[idx] if idx < len(fusion_result.corrected_midi) else None
        if rmvpe_midi is not None:
            hybrid_midi.append(rmvpe_midi)
            actions.append("rmvpe_primary")
            support_counts.append(None)
            supporters_by_frame.append([])
            rmvpe_primary_frames += 1
            continue
        if fusion_midi is None:
            hybrid_midi.append(None)
            actions.append("empty")
            support_counts.append(0)
            supporters_by_frame.append([])
            continue
        support_count, supporters = _hybrid_support_for_fusion_gap(idx, fusion_midi, series, cfg.hybrid_support_cents)
        support_counts.append(support_count)
        supporters_by_frame.append(supporters)
        accepted = support_count >= cfg.hybrid_min_support
        hybrid_midi.append(fusion_midi if accepted else None)
        actions.append("fusion_gap_fill_supported" if accepted else "fusion_gap_fill_rejected_weak_support")
        fusion_gap_fill_frames += 1 if accepted else 0
        fusion_gap_rejected_frames += 0 if accepted else 1
        fill_events.append({"time_sec": round(times[idx], 4), "fusion_midi": round(fusion_midi, 4), "support_count": support_count, "supporters": supporters, "decision": "accepted" if accepted else "rejected"})
    notes, note_stats = _build_notes("hybrid", times, hybrid_midi, hop_sec, cfg.note_change_cents, cfg.min_note_ms / 1000.0, cfg.merge_gap_ms / 1000.0, cfg.merge_pitch_cents)
    voiced_frames = sum(1 for v in hybrid_midi if v is not None)
    stats = {"target": "hybrid", "frames": len(hybrid_midi), "postprocessed_voiced_frames": voiced_frames, "postprocessed_voiced_ratio": round(voiced_frames / len(hybrid_midi), 6) if hybrid_midi else 0, "rmvpe_primary_frames": rmvpe_primary_frames, "fusion_gap_fill_frames": fusion_gap_fill_frames, "fusion_gap_rejected_frames": fusion_gap_rejected_frames, "hybrid_support_cents": cfg.hybrid_support_cents, "hybrid_min_support": cfg.hybrid_min_support, **note_stats}
    return {"name": "hybrid", "corrected_midi": hybrid_midi, "actions": actions, "support_counts": support_counts, "supporters_by_frame": supporters_by_frame, "fill_events": fill_events, "notes": notes, "stats": stats}


def _create_postprocessed_rows(original_headers: list[str], rows: list[dict[str, str]], results: dict[str, SeriesResult], hybrid_result: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    new_headers = list(original_headers)
    add_columns = ["rmvpe_postprocessed_f0_hz", "rmvpe_postprocessed_midi", "rmvpe_postprocess_action", "fusion_postprocessed_f0_hz", "fusion_postprocessed_midi", "fusion_postprocess_action", "hybrid_postprocessed_f0_hz", "hybrid_postprocessed_midi", "hybrid_postprocess_action", "hybrid_support_count", "hybrid_supporters"]
    for col in add_columns:
        if col not in new_headers:
            new_headers.append(col)
    out_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        out = dict(row)
        for target in ("rmvpe", "fusion"):
            result = results[target]
            midi = result.corrected_midi[idx]
            hz = _midi_to_hz(midi)
            out[f"{target}_postprocessed_f0_hz"] = "" if hz is None else round(hz, 6)
            out[f"{target}_postprocessed_midi"] = "" if midi is None else round(midi, 6)
            out[f"{target}_postprocess_action"] = result.actions[idx]
        hybrid_midi = hybrid_result["corrected_midi"][idx]
        hybrid_hz = _midi_to_hz(hybrid_midi)
        out["hybrid_postprocessed_f0_hz"] = "" if hybrid_hz is None else round(hybrid_hz, 6)
        out["hybrid_postprocessed_midi"] = "" if hybrid_midi is None else round(hybrid_midi, 6)
        out["hybrid_postprocess_action"] = hybrid_result["actions"][idx]
        support_count = hybrid_result["support_counts"][idx]
        out["hybrid_support_count"] = "" if support_count is None else support_count
        out["hybrid_supporters"] = ", ".join(hybrid_result["supporters_by_frame"][idx])
        out_rows.append(out)
    return new_headers, out_rows


def _summarize_source_diagnostics(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = {key: data[key] for key in ("status", "model_status", "model_weights", "voiced_ratio", "final_voiced_ratio", "average_confidence") if key in data}
    for model in REFERENCE_MODELS:
        for key, value in data.items():
            lower = str(key).lower()
            if model in lower and any(hint in lower for hint in ("weight", "voiced", "confidence", "status")):
                result[key] = value
    return result
