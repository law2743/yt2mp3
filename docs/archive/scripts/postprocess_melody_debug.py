#!/usr/bin/env python3
"""
Debug postprocess for yt2mp3 melody/fusion artifacts.

Place:
    scripts/postprocess_melody_debug_v2.py

Run:
    python scripts/postprocess_melody_debug_v2.py --artifact-root /tmp/yt2mp3-fusion-debug

Outputs:
    <artifact-root>/postprocess/comparison_postprocessed.csv
    <artifact-root>/postprocess/cleaned_melody.json
    <artifact-root>/postprocess/postprocess_diagnostics.json

Purpose:
    Produce two additional A/B test lines:
      - rmvpe_postprocessed_f0_hz
      - fusion_postprocessed_f0_hz
      - hybrid_postprocessed_f0_hz

Design:
    - RMVPE strict mode: do not fill RMVPE missing frames with other models.
    - Fusion clean mode: do not create new voiced frames in v1; only clean/correct existing fusion frames.
    - Correct likely octave jumps using local context + model support.
    - Remove too-short voiced islands.
    - Extract simplified note segments for diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "postprocess_melody_debug_v2_hybrid_20260629"

OFFICIAL_MODELS = ("fusion", "rmvpe", "torchcrepe", "fcpe", "pesto")
REFERENCE_MODELS = ("rmvpe", "torchcrepe", "fcpe", "pesto")

MODEL_WEIGHTS = {
    "rmvpe": 1.25,
    "torchcrepe": 1.05,
    "pesto": 0.90,
    "fcpe": 0.45,
}

TIME_COLUMNS = (
    "time_sec",
    "time_seconds",
    "time",
    "seconds",
    "sec",
    "timestamp",
    "t",
)

PITCH_HINTS = (
    "f0_hz",
    "pitch_hz",
    "frequency_hz",
    "freq_hz",
    "hz",
    "midi",
    "note_number",
    "f0",
    "pitch",
)

RAW_PITCH_PATTERNS = (
    re.compile(r"(^|[_\-.])raw[_\-.]?f0($|[_\-.])", re.I),
    re.compile(r"(^|[_\-.])raw[_\-.]?f0[_\-.]?hz($|[_\-.])", re.I),
    re.compile(r"(^|[_\-.])raw[_\-.]?pitch($|[_\-.])", re.I),
)


@dataclass
class ArtifactPaths:
    artifact_root: Path
    comparison_csv: Path
    fusion_csv: Path | None = None
    diagnostics_json: Path | None = None


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


def log(message: str, level: str = "INFO") -> None:
    icon = {"INFO": "🟩", "WARN": "🟨", "ERROR": "🟥", "DEBUG": "🟦"}.get(level, "🟦")
    now = datetime.now().strftime("%H:%M:%S")
    print(f"{icon} [{now}][{level}] {message}")


def safe_float(value: Any) -> float | None:
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


def hz_to_midi(hz: float | None) -> float | None:
    if hz is None or hz <= 0 or not math.isfinite(hz):
        return None
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_hz(midi: float | None) -> float | None:
    if midi is None or not math.isfinite(midi):
        return None
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def cents(a: float, b: float) -> float:
    return abs(a - b) * 100.0


def median(values: list[float]) -> float | None:
    cleaned = [v for v in values if v is not None and math.isfinite(v)]
    if not cleaned:
        return None
    return float(statistics.median(cleaned))


def choose_time_column(headers: list[str]) -> str | None:
    lower_map = {h.lower().strip(): h for h in headers}
    for name in TIME_COLUMNS:
        if name in lower_map:
            return lower_map[name]
    for h in headers:
        lower = h.lower().strip()
        if "time" in lower and "timeout" not in lower:
            return h
    return None


def is_raw_pitch_column(header: str) -> bool:
    lower = header.lower().strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    if normalized in {"raw_f0_hz", "raw_f0", "raw_pitch_hz", "raw_pitch"}:
        return True
    return any(pattern.search(lower) or pattern.search(normalized) for pattern in RAW_PITCH_PATTERNS)


def looks_like_hz(column: str, values: list[float]) -> bool:
    lower = column.lower().strip()
    if "midi" in lower or "note_number" in lower:
        return False
    if any(hint in lower for hint in ("hz", "f0", "freq", "frequency")):
        return True
    if not values:
        return False
    try:
        return statistics.median(values) > 125
    except statistics.StatisticsError:
        return False


def find_pitch_column(headers: list[str], model: str) -> str | None:
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
        if is_raw_pitch_column(h):
            continue
        if any(skip in lower for skip in ("confidence", "conf", "prob", "score", "voiced", "delta", "diff", "error", "range", "spread")):
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

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return headers, rows


def write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def is_ignored_path(path: Path) -> bool:
    text = str(path).replace("\\", "/").lower()
    return (
        "/tmp/pytest-" in text
        or "/tmp/pytest-of-" in text
        or "/node_modules/" in text
        or "/site-packages/" in text
    )


def iter_files(root: Path, name: str) -> Iterable[Path]:
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if is_ignored_path(current):
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".")
            and d not in {".git", ".venv", "venv", "env", "node_modules", "__pycache__"}
            and not d.startswith("pytest-")
        ]
        for filename in filenames:
            if filename == name:
                path = current / filename
                if not is_ignored_path(path):
                    yield path


def guess_artifact_root_from_comparison(path: Path) -> Path:
    parts = [p.lower() for p in path.parts]
    if "analysis" in parts:
        idx = parts.index("analysis")
        return Path(*path.parts[:idx])
    return path.parent


def find_artifacts(
    comparison_csv: Path | None,
    fusion_csv: Path | None,
    artifact_roots: list[Path],
) -> ArtifactPaths:
    if comparison_csv:
        comparison_csv = comparison_csv.expanduser().resolve()
        if not comparison_csv.exists():
            raise SystemExit(f"comparison CSV not found: {comparison_csv}")
        root = guess_artifact_root_from_comparison(comparison_csv)
        return ArtifactPaths(
            artifact_root=root,
            comparison_csv=comparison_csv,
            fusion_csv=fusion_csv.expanduser().resolve() if fusion_csv else None,
            diagnostics_json=find_optional(root, "diagnostics.json"),
        )

    search_roots: list[Path] = []
    for root in artifact_roots:
        p = root.expanduser().resolve()
        if p.exists() and p not in search_roots:
            search_roots.append(p)

    for common in (
        "/tmp/yt2mp3-fusion-debug",
        "/tmp/yks",
        "/tmp/yt2mp3",
        "/tmp/yt2mp3-jobs",
        "/tmp/yt2mp3_jobs",
        "/tmp/jobs",
    ):
        p = Path(common)
        if p.exists() and p not in search_roots:
            search_roots.append(p)

    if not search_roots:
        search_roots.append(Path.cwd())

    candidates: list[tuple[float, int, Path]] = []
    for root in search_roots:
        for path in iter_files(root, "comparison.csv"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            full = str(path).replace("\\", "/").lower()
            bonus = 0
            if "/tmp/yt2mp3-fusion-debug/" in full:
                bonus += 100
            if "/analysis/melody/fusion/" in full:
                bonus += 50
            candidates.append((mtime, bonus, path))

    if not candidates:
        roots = "\n".join(f"  - {p}" for p in search_roots)
        raise SystemExit(
            "comparison.csv not found.\n"
            "Searched roots:\n"
            f"{roots}\n\n"
            "Try:\n"
            "  python scripts/postprocess_melody_debug_v2.py --artifact-root /tmp/yt2mp3-fusion-debug\n"
            "or:\n"
            "  python scripts/postprocess_melody_debug_v2.py --comparison-csv /path/to/comparison.csv"
        )

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = candidates[0][2]
    root = guess_artifact_root_from_comparison(selected)
    return ArtifactPaths(
        artifact_root=root,
        comparison_csv=selected,
        fusion_csv=find_optional(root, "fusion.csv"),
        diagnostics_json=find_optional(root, "diagnostics.json"),
    )


def find_optional(root: Path, name: str) -> Path | None:
    direct_candidates = [
        root / name,
        root / "analysis" / "melody" / "fusion" / name,
        root / "postprocess" / name,
    ]
    for path in direct_candidates:
        if path.exists():
            return path
    newest: tuple[float, Path] | None = None
    for path in iter_files(root, name):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if newest is None or mtime > newest[0]:
            newest = (mtime, path)
    return newest[1] if newest else None


def load_official_series(headers: list[str], rows: list[dict[str, str]]) -> tuple[str, dict[str, list[float | None]], dict[str, str]]:
    time_col = choose_time_column(headers)
    if time_col is None:
        raise SystemExit("comparison.csv does not contain a recognizable time column.")

    column_by_model: dict[str, str] = {}
    for model in OFFICIAL_MODELS:
        col = find_pitch_column(headers, model)
        if col:
            column_by_model[model] = col

    required = {"fusion", "rmvpe"}
    missing_required = sorted(required - set(column_by_model))
    if missing_required:
        raise SystemExit(
            f"comparison.csv missing required pitch columns for: {', '.join(missing_required)}\n"
            f"Detected columns: {column_by_model}"
        )

    series: dict[str, list[float | None]] = {}
    for model, col in column_by_model.items():
        raw_values: list[float] = []
        parsed: list[float | None] = []
        for row in rows:
            value = safe_float(row.get(col))
            parsed.append(value)
            if value is not None and value > 0:
                raw_values.append(value)

        convert_hz = looks_like_hz(col, raw_values)
        midi_values: list[float | None] = []
        for value in parsed:
            if value is None or value <= 0:
                midi_values.append(None)
                continue
            midi = hz_to_midi(value) if convert_hz else value
            if midi is None or midi < 20 or midi > 110:
                midi_values.append(None)
            else:
                midi_values.append(midi)
        series[model] = midi_values

    return time_col, series, column_by_model


def estimate_hop(times: list[float]) -> float:
    diffs = [
        times[i + 1] - times[i]
        for i in range(len(times) - 1)
        if times[i + 1] > times[i]
    ]
    if not diffs:
        return 0.01
    return float(statistics.median(diffs))


def find_octave_candidates(
    times: list[float],
    series: dict[str, list[float | None]],
    octave_tolerance_cents: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    targets = (1200.0, 2400.0, 3600.0)

    for idx, t in enumerate(times):
        values: list[tuple[str, float]] = []
        for model in OFFICIAL_MODELS:
            values_for_model = series.get(model)
            if not values_for_model:
                continue
            y = values_for_model[idx]
            if y is not None:
                values.append((model, y))

        if len(values) < 2:
            continue

        all_models = ", ".join(f"{name}={midi:.2f}" for name, midi in values)
        for i, (a_name, a_midi) in enumerate(values):
            for b_name, b_midi in values[i + 1:]:
                diff = cents(a_midi, b_midi)
                target = min(targets, key=lambda x: abs(diff - x))
                error = abs(diff - target)
                if error <= octave_tolerance_cents:
                    rows.append({
                        "time_sec": round(t, 4),
                        "model_a": a_name,
                        "midi_a": round(a_midi, 4),
                        "model_b": b_name,
                        "midi_b": round(b_midi, 4),
                        "diff_cents": round(diff, 2),
                        "nearest_octave_cents": int(target),
                        "octave_error_cents": round(error, 2),
                        "all_models": all_models,
                    })

    rows.sort(key=lambda r: (float(r["octave_error_cents"]), -float(r["diff_cents"])))
    return rows


def local_context_median(
    idx: int,
    times: list[float],
    values: list[float | None],
    processed_prefix: list[float | None],
    window_sec: float,
) -> float | None:
    context: list[float] = []
    t = times[idx]

    # Previous processed values are more useful because corrections become continuous.
    j = idx - 1
    while j >= 0 and t - times[j] <= window_sec:
        y = processed_prefix[j] if j < len(processed_prefix) else values[j]
        if y is not None:
            context.append(y)
        j -= 1

    # Future raw values give lookahead without needing a complex smoother.
    j = idx + 1
    while j < len(values) and times[j] - t <= window_sec:
        y = values[j]
        if y is not None:
            context.append(y)
        j += 1

    return median(context)


def support_score(
    candidate_midi: float,
    idx: int,
    target_name: str,
    series: dict[str, list[float | None]],
    support_cents: float,
) -> tuple[float, int, list[str]]:
    score = 0.0
    count = 0
    names: list[str] = []

    # Do not include fusion as evidence for fusion. Do not include target itself.
    for model in REFERENCE_MODELS:
        if model == target_name:
            continue
        values = series.get(model)
        if not values:
            continue
        y = values[idx]
        if y is None:
            continue

        distance = cents(candidate_midi, y)
        weight = MODEL_WEIGHTS.get(model, 0.7)
        if distance <= support_cents:
            score += 1.0 * weight
            count += 1
            names.append(model)
        elif distance <= support_cents * 2:
            score += 0.45 * weight
            names.append(f"{model}?")

    return score, count, names


def correction_score(
    candidate_midi: float,
    shift: int,
    original_midi: float,
    idx: int,
    target_name: str,
    times: list[float],
    target_values: list[float | None],
    processed_prefix: list[float | None],
    series: dict[str, list[float | None]],
    support_cents: float,
    context_window_sec: float,
) -> tuple[float, dict[str, Any]]:
    score = 0.0
    details: dict[str, Any] = {}

    support, support_count, supporters = support_score(candidate_midi, idx, target_name, series, support_cents)
    score += support
    details["support_score"] = round(support, 4)
    details["support_count"] = support_count
    details["supporters"] = supporters

    context = local_context_median(idx, times, target_values, processed_prefix, context_window_sec)
    details["context_midi"] = round(context, 4) if context is not None else None

    if context is not None:
        dist = cents(candidate_midi, context)
        details["context_distance_cents"] = round(dist, 2)
        if dist <= support_cents:
            score += 1.10
        elif dist <= support_cents * 2:
            score += 0.55
        elif dist <= 300:
            score += 0.10
        else:
            score -= min(1.5, (dist - 300) / 600)

    # Prefer no correction unless evidence is meaningful.
    if shift == 0:
        score += 0.35
    elif abs(shift) == 12:
        score -= 0.15
    else:
        score -= 0.45

    # Penalize very large jump from original unless the shift is exactly octave-like.
    details["shift"] = shift
    details["candidate_midi"] = round(candidate_midi, 4)
    details["original_midi"] = round(original_midi, 4)
    details["score"] = round(score, 4)
    return score, details


def correct_octaves_for_target(
    name: str,
    times: list[float],
    target_values: list[float | None],
    series: dict[str, list[float | None]],
    support_cents: float,
    context_window_sec: float,
    rmvpe_strict: bool,
) -> tuple[list[float | None], list[str], list[dict[str, Any]]]:
    corrected: list[float | None] = []
    actions: list[str] = []
    corrections: list[dict[str, Any]] = []

    # RMVPE should be harder to change because earlier diagnostics show it is the most stable anchor.
    min_improvement = 1.20 if name == "rmvpe" and rmvpe_strict else 0.60
    min_support_for_change = 2 if name == "rmvpe" and rmvpe_strict else 1

    for idx, original in enumerate(target_values):
        if original is None:
            corrected.append(None)
            actions.append("empty")
            continue

        candidate_shifts = (-24, -12, 0, 12, 24)
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for shift in candidate_shifts:
            candidate = original + shift
            score, details = correction_score(
                candidate_midi=candidate,
                shift=shift,
                original_midi=original,
                idx=idx,
                target_name=name,
                times=times,
                target_values=target_values,
                processed_prefix=corrected,
                series=series,
                support_cents=support_cents,
                context_window_sec=context_window_sec,
            )
            scored.append((score, shift, details))

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_shift, best_details = scored[0]
        original_score = next(score for score, shift, _ in scored if shift == 0)
        improvement = best_score - original_score

        allow_change = (
            best_shift != 0
            and improvement >= min_improvement
            and best_details.get("support_count", 0) >= min_support_for_change
        )

        if allow_change:
            new_value = original + best_shift
            corrected.append(new_value)
            actions.append(f"octave_shift_{best_shift:+d}")
            corrections.append({
                "time_sec": round(times[idx], 4),
                "target": name,
                "old_midi": round(original, 4),
                "new_midi": round(new_value, 4),
                "shift": best_shift,
                "improvement": round(improvement, 4),
                **best_details,
            })
        else:
            corrected.append(original)
            actions.append("kept")

    return corrected, actions, corrections


def remove_short_voiced_islands(
    name: str,
    times: list[float],
    values: list[float | None],
    actions: list[str],
    min_island_sec: float,
    hop_sec: float,
) -> tuple[list[float | None], list[str], list[dict[str, Any]]]:
    cleaned = list(values)
    new_actions = list(actions)
    removed: list[dict[str, Any]] = []

    start: int | None = None
    for idx, value in enumerate(values + [None]):
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
                removed.append({
                    "target": name,
                    "start_sec": round(times[start], 4),
                    "end_sec": round(times[end], 4),
                    "duration_sec": round(duration, 4),
                    "frames": end - start + 1,
                    "median_midi": round(statistics.median(segment_values), 4) if segment_values else None,
                })
            start = None

    return cleaned, new_actions, removed


def build_notes(
    name: str,
    times: list[float],
    values: list[float | None],
    hop_sec: float,
    note_change_cents: float,
    min_note_sec: float,
    merge_gap_sec: float,
    merge_pitch_cents: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        raw_notes.append({
            "start_sec": round(start, 4),
            "end_sec": round(end, 4),
            "duration_sec": round(max(0.0, end - start), 4),
            "midi": round(med, 4),
            "f0_hz": round(midi_to_hz(med) or 0.0, 4),
            "frames": len(segment_values),
        })
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
        if cents(midi, last_midi) > note_change_cents:
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
            # Try to merge into previous if pitch and gap are close.
            if merged:
                prev = merged[-1]
                gap = float(note["start_sec"]) - float(prev["end_sec"])
                pitch_dist = cents(float(note["midi"]), float(prev["midi"]))
                if gap <= merge_gap_sec and pitch_dist <= merge_pitch_cents:
                    total_duration = float(prev["duration_sec"]) + float(note["duration_sec"])
                    if total_duration > 0:
                        prev_weight = float(prev["duration_sec"]) / total_duration
                        note_weight = float(note["duration_sec"]) / total_duration
                        new_midi = float(prev["midi"]) * prev_weight + float(note["midi"]) * note_weight
                    else:
                        new_midi = float(prev["midi"])
                    prev["end_sec"] = note["end_sec"]
                    prev["duration_sec"] = round(float(prev["end_sec"]) - float(prev["start_sec"]), 4)
                    prev["midi"] = round(new_midi, 4)
                    prev["f0_hz"] = round(midi_to_hz(new_midi) or 0.0, 4)
                    prev["frames"] = int(prev.get("frames", 0)) + int(note.get("frames", 0))
                    prev["merged_short_note"] = True
                    removed_short_notes += 1
                    continue

            # If it cannot be merged, drop it from notes.
            removed_short_notes += 1
            continue

        merged.append(note)

    stats = {
        "target": name,
        "raw_note_count": len(raw_notes),
        "cleaned_note_count": len(merged),
        "removed_or_merged_short_notes": removed_short_notes,
        "min_note_sec": min_note_sec,
        "note_change_cents": note_change_cents,
    }
    return merged, stats


def process_target(
    name: str,
    times: list[float],
    series: dict[str, list[float | None]],
    args: argparse.Namespace,
    hop_sec: float,
) -> SeriesResult:
    original = series[name]
    corrected, actions, corrections = correct_octaves_for_target(
        name=name,
        times=times,
        target_values=original,
        series=series,
        support_cents=args.support_cents,
        context_window_sec=args.context_window_sec,
        rmvpe_strict=args.rmvpe_strict,
    )

    cleaned, actions, removed_islands = remove_short_voiced_islands(
        name=name,
        times=times,
        values=corrected,
        actions=actions,
        min_island_sec=args.min_voiced_island_ms / 1000.0,
        hop_sec=hop_sec,
    )

    notes, note_stats = build_notes(
        name=name,
        times=times,
        values=cleaned,
        hop_sec=hop_sec,
        note_change_cents=args.note_change_cents,
        min_note_sec=args.min_note_ms / 1000.0,
        merge_gap_sec=args.merge_gap_ms / 1000.0,
        merge_pitch_cents=args.merge_pitch_cents,
    )

    original_voiced = sum(1 for v in original if v is not None)
    cleaned_voiced = sum(1 for v in cleaned if v is not None)

    stats = {
        "target": name,
        "frames": len(original),
        "original_voiced_frames": original_voiced,
        "postprocessed_voiced_frames": cleaned_voiced,
        "original_voiced_ratio": round(original_voiced / len(original), 6) if original else 0,
        "postprocessed_voiced_ratio": round(cleaned_voiced / len(cleaned), 6) if cleaned else 0,
        "octave_correction_count": len(corrections),
        "removed_short_island_count": len(removed_islands),
        **note_stats,
    }

    return SeriesResult(
        name=name,
        original_midi=original,
        corrected_midi=cleaned,
        actions=actions,
        corrections=corrections,
        removed_islands=removed_islands,
        notes=notes,
        stats=stats,
    )


def hybrid_support_for_fusion_gap(
    idx: int,
    fusion_midi: float,
    series: dict[str, list[float | None]],
    support_cents: float,
) -> tuple[int, list[str]]:
    supporters: list[str] = []
    for model in ("torchcrepe", "fcpe", "pesto"):
        values = series.get(model)
        if not values or idx >= len(values):
            continue
        y = values[idx]
        if y is None:
            continue
        if cents(fusion_midi, y) <= support_cents:
            supporters.append(model)
    return len(supporters), supporters


def build_hybrid_result(
    times: list[float],
    series: dict[str, list[float | None]],
    rmvpe_result: SeriesResult,
    fusion_result: SeriesResult,
    args: argparse.Namespace,
    hop_sec: float,
) -> dict[str, Any]:
    hybrid_midi: list[float | None] = []
    actions: list[str] = []
    support_counts: list[int | None] = []
    supporters_by_frame: list[list[str]] = []
    fill_events: list[dict[str, Any]] = []

    rmvpe_primary_frames = 0
    fusion_gap_fill_frames = 0
    fusion_gap_rejected_frames = 0

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

        support_count, supporters = hybrid_support_for_fusion_gap(
            idx=idx,
            fusion_midi=fusion_midi,
            series=series,
            support_cents=args.hybrid_support_cents,
        )
        support_counts.append(support_count)
        supporters_by_frame.append(supporters)

        if support_count >= args.hybrid_min_support:
            hybrid_midi.append(fusion_midi)
            actions.append("fusion_gap_fill_supported")
            fusion_gap_fill_frames += 1
            fill_events.append({
                "time_sec": round(times[idx], 4),
                "fusion_midi": round(fusion_midi, 4),
                "support_count": support_count,
                "supporters": supporters,
                "decision": "accepted",
            })
        else:
            hybrid_midi.append(None)
            actions.append("fusion_gap_fill_rejected_weak_support")
            fusion_gap_rejected_frames += 1
            fill_events.append({
                "time_sec": round(times[idx], 4),
                "fusion_midi": round(fusion_midi, 4),
                "support_count": support_count,
                "supporters": supporters,
                "decision": "rejected",
            })

    notes, note_stats = build_notes(
        name="hybrid",
        times=times,
        values=hybrid_midi,
        hop_sec=hop_sec,
        note_change_cents=args.note_change_cents,
        min_note_sec=args.min_note_ms / 1000.0,
        merge_gap_sec=args.merge_gap_ms / 1000.0,
        merge_pitch_cents=args.merge_pitch_cents,
    )

    voiced_frames = sum(1 for v in hybrid_midi if v is not None)
    stats = {
        "target": "hybrid",
        "frames": len(hybrid_midi),
        "postprocessed_voiced_frames": voiced_frames,
        "postprocessed_voiced_ratio": round(voiced_frames / len(hybrid_midi), 6) if hybrid_midi else 0,
        "rmvpe_primary_frames": rmvpe_primary_frames,
        "fusion_gap_fill_frames": fusion_gap_fill_frames,
        "fusion_gap_rejected_frames": fusion_gap_rejected_frames,
        "hybrid_support_cents": args.hybrid_support_cents,
        "hybrid_min_support": args.hybrid_min_support,
        **note_stats,
    }

    return {
        "name": "hybrid",
        "corrected_midi": hybrid_midi,
        "actions": actions,
        "support_counts": support_counts,
        "supporters_by_frame": supporters_by_frame,
        "fill_events": fill_events,
        "notes": notes,
        "stats": stats,
    }


def create_postprocessed_rows(
    original_headers: list[str],
    rows: list[dict[str, str]],
    results: dict[str, SeriesResult],
    hybrid_result: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    new_headers = list(original_headers)

    add_columns = [
        "rmvpe_postprocessed_f0_hz",
        "rmvpe_postprocessed_midi",
        "rmvpe_postprocess_action",
        "fusion_postprocessed_f0_hz",
        "fusion_postprocessed_midi",
        "fusion_postprocess_action",
        "hybrid_postprocessed_f0_hz",
        "hybrid_postprocessed_midi",
        "hybrid_postprocess_action",
        "hybrid_support_count",
        "hybrid_supporters",
    ]
    for col in add_columns:
        if col not in new_headers:
            new_headers.append(col)

    out_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        out = dict(row)
        for target in ("rmvpe", "fusion"):
            result = results[target]
            midi = result.corrected_midi[idx]
            hz = midi_to_hz(midi)
            out[f"{target}_postprocessed_f0_hz"] = "" if hz is None else round(hz, 6)
            out[f"{target}_postprocessed_midi"] = "" if midi is None else round(midi, 6)
            out[f"{target}_postprocess_action"] = result.actions[idx]

        hybrid_midi = hybrid_result["corrected_midi"][idx]
        hybrid_hz = midi_to_hz(hybrid_midi)
        out["hybrid_postprocessed_f0_hz"] = "" if hybrid_hz is None else round(hybrid_hz, 6)
        out["hybrid_postprocessed_midi"] = "" if hybrid_midi is None else round(hybrid_midi, 6)
        out["hybrid_postprocess_action"] = hybrid_result["actions"][idx]
        support_count = hybrid_result["support_counts"][idx]
        out["hybrid_support_count"] = "" if support_count is None else support_count
        out["hybrid_supporters"] = ", ".join(hybrid_result["supporters_by_frame"][idx])
        out_rows.append(out)

    return new_headers, out_rows


def load_json(path: Path | None) -> Any | None:
    if path is None or not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Postprocess yt2mp3 melody fusion debug artifacts.")
    parser.add_argument("--artifact-root", type=Path, action="append", default=[], help="Artifact root to scan, e.g. /tmp/yt2mp3-fusion-debug")
    parser.add_argument("--comparison-csv", type=Path, default=None, help="Direct path to comparison.csv")
    parser.add_argument("--fusion-csv", type=Path, default=None, help="Optional direct path to fusion.csv")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory. Defaults to <artifact-root>/postprocess")

    parser.add_argument("--support-cents", type=float, default=60.0)
    parser.add_argument("--octave-tolerance-cents", type=float, default=125.0)
    parser.add_argument("--context-window-sec", type=float, default=0.80)

    parser.add_argument("--min-voiced-island-ms", type=float, default=80.0)
    parser.add_argument("--min-note-ms", type=float, default=100.0)
    parser.add_argument("--note-change-cents", type=float, default=80.0)
    parser.add_argument("--merge-gap-ms", type=float, default=80.0)
    parser.add_argument("--merge-pitch-cents", type=float, default=100.0)

    parser.add_argument(
        "--hybrid-support-cents",
        type=float,
        default=60.0,
        help="Max cents distance for torchcrepe/fcpe/pesto to support a fusion gap fill.",
    )
    parser.add_argument(
        "--hybrid-min-support",
        type=int,
        default=2,
        help="Minimum number of torchcrepe/fcpe/pesto supporters required to fill an RMVPE gap with fusion.",
    )

    parser.add_argument(
        "--rmvpe-strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Do not fill RMVPE missing frames; make RMVPE octave correction conservative.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifacts = find_artifacts(
        comparison_csv=args.comparison_csv,
        fusion_csv=args.fusion_csv,
        artifact_roots=args.artifact_root,
    )

    out_dir = args.out_dir.expanduser().resolve() if args.out_dir else artifacts.artifact_root / "postprocess"
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"script version: {SCRIPT_VERSION}")
    log(f"artifact root: {artifacts.artifact_root}")
    log(f"comparison csv: {artifacts.comparison_csv}")
    if artifacts.fusion_csv:
        log(f"fusion csv: {artifacts.fusion_csv}")
    if artifacts.diagnostics_json:
        log(f"diagnostics json: {artifacts.diagnostics_json}")
    log(f"output dir: {out_dir}")

    headers, rows = read_csv(artifacts.comparison_csv)
    time_col, series, column_by_model = load_official_series(headers, rows)

    times: list[float] = []
    for idx, row in enumerate(rows):
        t = safe_float(row.get(time_col))
        if t is None:
            # Preserve row count; assume regular grid if time missing.
            t = float(idx)
        times.append(t)

    hop_sec = estimate_hop(times)
    octave_candidates = find_octave_candidates(
        times=times,
        series=series,
        octave_tolerance_cents=args.octave_tolerance_cents,
    )

    log(f"time column: {time_col}")
    log(f"detected pitch columns: {column_by_model}")
    log(f"rows: {len(rows)}, estimated hop: {hop_sec:.5f}s")
    log(f"octave candidates: {len(octave_candidates)}")

    results: dict[str, SeriesResult] = {}
    for target in ("rmvpe", "fusion"):
        results[target] = process_target(
            name=target,
            times=times,
            series=series,
            args=args,
            hop_sec=hop_sec,
        )
        stats = results[target].stats
        log(
            f"{target}: octave corrections={stats['octave_correction_count']}, "
            f"removed islands={stats['removed_short_island_count']}, "
            f"notes {stats['raw_note_count']} -> {stats['cleaned_note_count']}"
        )

    hybrid_result = build_hybrid_result(
        times=times,
        series=series,
        rmvpe_result=results["rmvpe"],
        fusion_result=results["fusion"],
        args=args,
        hop_sec=hop_sec,
    )
    hybrid_stats = hybrid_result["stats"]
    log(
        "hybrid: "
        f"rmvpe primary={hybrid_stats['rmvpe_primary_frames']}, "
        f"fusion gap fills={hybrid_stats['fusion_gap_fill_frames']}, "
        f"fusion rejected={hybrid_stats['fusion_gap_rejected_frames']}, "
        f"notes {hybrid_stats['raw_note_count']} -> {hybrid_stats['cleaned_note_count']}"
    )

    out_headers, out_rows = create_postprocessed_rows(headers, rows, results, hybrid_result)
    comparison_out = out_dir / "comparison_postprocessed.csv"
    write_csv(comparison_out, out_headers, out_rows)

    cleaned_melody = {
        "version": SCRIPT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "artifact_root": str(artifacts.artifact_root),
            "comparison_csv": str(artifacts.comparison_csv),
            "fusion_csv": str(artifacts.fusion_csv) if artifacts.fusion_csv else None,
        },
        "tracks": {
            "rmvpe_postprocessed": {
                "mode": "rmvpe_strict_no_gap_fill" if args.rmvpe_strict else "rmvpe_non_strict",
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
    cleaned_melody_path = out_dir / "cleaned_melody.json"
    write_json(cleaned_melody_path, cleaned_melody)

    diagnostics_data = load_json(artifacts.diagnostics_json)
    postprocess_diagnostics = {
        "version": SCRIPT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "artifact_root": str(artifacts.artifact_root),
            "comparison_csv": str(artifacts.comparison_csv),
            "fusion_csv": str(artifacts.fusion_csv) if artifacts.fusion_csv else None,
            "diagnostics_json": str(artifacts.diagnostics_json) if artifacts.diagnostics_json else None,
        },
        "parameters": {
            "rmvpe_strict": args.rmvpe_strict,
            "support_cents": args.support_cents,
            "octave_tolerance_cents": args.octave_tolerance_cents,
            "context_window_sec": args.context_window_sec,
            "min_voiced_island_ms": args.min_voiced_island_ms,
            "min_note_ms": args.min_note_ms,
            "note_change_cents": args.note_change_cents,
            "merge_gap_ms": args.merge_gap_ms,
            "merge_pitch_cents": args.merge_pitch_cents,
            "hybrid_support_cents": args.hybrid_support_cents,
            "hybrid_min_support": args.hybrid_min_support,
        },
        "columns": column_by_model,
        "time_column": time_col,
        "estimated_hop_sec": hop_sec,
        "octave_candidates_count": len(octave_candidates),
        "octave_candidates_top": octave_candidates[:200],
        "targets": {
            **{
                target: {
                    "stats": result.stats,
                    "octave_corrections": result.corrections[:500],
                    "removed_short_islands": result.removed_islands[:500],
                }
                for target, result in results.items()
            },
            "hybrid": {
                "stats": hybrid_result["stats"],
                "fill_events": hybrid_result["fill_events"][:500],
            },
        },
        "source_diagnostics_summary": summarize_source_diagnostics(diagnostics_data),
    }
    diagnostics_out = out_dir / "postprocess_diagnostics.json"
    write_json(diagnostics_out, postprocess_diagnostics)

    log(f"wrote: {comparison_out}")
    log(f"wrote: {cleaned_melody_path}")
    log(f"wrote: {diagnostics_out}")
    return 0


def summarize_source_diagnostics(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    result: dict[str, Any] = {}
    for key in ("status", "model_status", "model_weights", "voiced_ratio", "final_voiced_ratio", "average_confidence"):
        if key in data:
            result[key] = data[key]

    # Keep this intentionally shallow to avoid copying very large diagnostics.
    for model in REFERENCE_MODELS:
        for key, value in data.items():
            lower = str(key).lower()
            if model in lower and any(hint in lower for hint in ("weight", "voiced", "confidence", "status")):
                result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
