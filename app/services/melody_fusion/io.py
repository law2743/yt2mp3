from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .schema import PitchPoint, PitchTrack
from .utils import median_step, robust_normalize, round_time, safe_bool, safe_float, quantile

PITCH_CSV_FIELDS = (
    "time_sec",
    "f0_hz",
    "raw_f0_hz",
    "confidence",
    "confidence_kind",
    "voiced",
    "backend",
)

FUSION_CSV_FIELDS = (
    "time_sec",
    "f0_hz",
    "raw_f0_hz",
    "confidence",
    "agreement",
    "support_count",
    "midi",
    "voiced",
    "backend",
)


def load_pitch_csv(path: str | Path, *, backend: str | None = None) -> PitchTrack:
    p = Path(path)
    rows: list[dict[str, str]] = []
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [field for field in PITCH_CSV_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{p}: missing CSV columns: {', '.join(missing)}")
        rows = list(reader)

    inferred_backend = backend or _infer_backend(p, rows)
    times: list[float] = []
    pre_frames: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        time_sec = round_time(safe_float(row.get("time_sec"), index * 0.01))
        f0_hz = safe_float(row.get("f0_hz"), 0.0)
        raw_f0_hz = safe_float(row.get("raw_f0_hz"), f0_hz)
        voiced = _csv_bool(row.get("voiced")) and f0_hz > 0.0
        raw_confidence = _optional_float(row.get("confidence"))
        confidence_kind = (row.get("confidence_kind") or "none").strip() or "none"
        times.append(time_sec)
        pre_frames.append(
            {
                "backend": inferred_backend,
                "time_sec": time_sec,
                "f0_hz": f0_hz,
                "raw_f0_hz": raw_f0_hz,
                "voiced": voiced,
                "raw_confidence": raw_confidence,
                "confidence_kind": confidence_kind,
            }
        )

    frame_step_sec = median_step(times, fallback=0.01)
    conf_values = [x["raw_confidence"] for x in pre_frames if x["raw_confidence"] is not None]
    low = quantile(conf_values, 0.10) if conf_values else 0.0
    high = quantile(conf_values, 0.90) if conf_values else 1.0

    frames: list[PitchPoint] = []
    for x in pre_frames:
        raw_conf = x["raw_confidence"]
        if raw_conf is None:
            norm_conf = 0.0
        else:
            norm_conf = robust_normalize(float(raw_conf), low, high)
            if not x["voiced"]:
                norm_conf *= 0.25
        frames.append(
            PitchPoint(
                backend=x["backend"],
                time_sec=x["time_sec"],
                f0_hz=x["f0_hz"],
                raw_f0_hz=x["raw_f0_hz"],
                voiced=x["voiced"],
                raw_confidence=raw_conf,
                confidence_kind=x["confidence_kind"],
                normalized_confidence=float(norm_conf),
            )
        )

    return PitchTrack(
        backend=inferred_backend,
        source_path=str(p),
        sample_rate=None,
        hop_size=None,
        frame_step_sec=frame_step_sec,
        frames=frames,
        original={"rows": len(rows), "confidence_kind": _dominant_confidence_kind(pre_frames)},
    )


def write_pitch_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PITCH_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_pitch_csv_row(row))


def save_fusion_csv(path: str | Path, frames: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FUSION_CSV_FIELDS)
        writer.writeheader()
        for frame in frames:
            writer.writerow(
                {
                    "time_sec": frame.get("time_sec", 0.0),
                    "f0_hz": frame.get("f0_hz", 0.0),
                    "raw_f0_hz": frame.get("raw_f0_hz", 0.0),
                    "confidence": frame.get("confidence", 0.0),
                    "agreement": frame.get("agreement", 0.0),
                    "support_count": frame.get("support_count", 0),
                    "midi": frame.get("midi", 0.0),
                    "voiced": 1 if frame.get("voiced") else 0,
                    "backend": frame.get("backend", "adaptive_fusion"),
                }
            )


def build_output_json(
    *,
    frames: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    sample_rate: int | None,
    hop_size: int | None,
    frame_step_sec: float,
    confidence_threshold: float,
) -> dict[str, Any]:
    voiced_count = sum(1 for fr in frames if fr.get("voiced"))
    duration_seconds = frames[-1]["time_sec"] if frames else 0.0
    return {
        "backend": "adaptive_fusion",
        "status": "ok",
        "sample_rate": sample_rate,
        "hop_size": hop_size,
        "frame_period_ms": round(frame_step_sec * 1000.0, 6),
        "confidence_threshold": confidence_threshold,
        "num_frames": len(frames),
        "frame_count": len(frames),
        "voiced_count": voiced_count,
        "voiced_ratio": round(voiced_count / len(frames), 6) if frames else 0.0,
        "duration_seconds": round(duration_seconds, 6),
        "frames": frames,
        "fusion": diagnostics,
    }


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def save_diagnostics(path: str | Path, payload: dict[str, Any]) -> None:
    save_json(path, payload)


def pitch_csv_rows_from_rmvpe_json(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    rows: list[dict[str, Any]] = []
    for point in data.get("points", []):
        if not isinstance(point, dict):
            continue
        confidence = _optional_float(point.get("confidence"))
        voiced = bool(point.get("voiced")) and safe_float(point.get("frequency_hz"), 0.0) > 0.0
        raw_f0 = safe_float(point.get("frequency_hz"), 0.0)
        rows.append(
            {
                "time_sec": safe_float(point.get("time"), len(rows) * 0.01),
                "f0_hz": raw_f0 if voiced else 0.0,
                "raw_f0_hz": raw_f0,
                "confidence": confidence,
                "confidence_kind": "voicing",
                "voiced": voiced,
                "backend": "rmvpe",
            }
        )
    return rows


def summarize_pitch_csv(path: str | Path, *, backend: str) -> dict[str, Any]:
    p = Path(path)
    track = load_pitch_csv(p, backend=backend)
    voiced_count = sum(1 for frame in track.frames if frame.voiced)
    confidence_kinds = sorted(
        {frame.confidence_kind or "none" for frame in track.frames}
    ) or ["none"]
    missing_confidence = sum(1 for frame in track.frames if frame.raw_confidence is None)
    return {
        "backend": backend,
        "status": "succeeded",
        "input_path": str(p),
        "rows": track.frame_count,
        "confidence_kind": confidence_kinds[0] if len(confidence_kinds) == 1 else ",".join(confidence_kinds),
        "missing_confidence_rows": missing_confidence,
        "voiced_ratio": round(voiced_count / track.frame_count, 6) if track.frame_count else 0.0,
    }


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = float(value)
    except Exception:
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def _csv_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return safe_bool(value)


def _infer_backend(path: Path, rows: list[dict[str, str]]) -> str:
    for row in rows:
        backend = (row.get("backend") or "").strip()
        if backend:
            return backend
    return path.stem


def _dominant_confidence_kind(rows: list[dict[str, Any]]) -> str:
    kinds = [row.get("confidence_kind") or "none" for row in rows]
    if not kinds:
        return "none"
    return max(set(kinds), key=kinds.count)


def _pitch_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    confidence = row.get("confidence")
    return {
        "time_sec": round(float(row.get("time_sec", 0.0)), 6),
        "f0_hz": round(float(row.get("f0_hz", 0.0) or 0.0), 6),
        "raw_f0_hz": round(float(row.get("raw_f0_hz", row.get("f0_hz", 0.0)) or 0.0), 6),
        "confidence": "" if confidence is None else round(float(confidence), 6),
        "confidence_kind": row.get("confidence_kind") or "none",
        "voiced": 1 if row.get("voiced") else 0,
        "backend": row.get("backend") or "",
    }
