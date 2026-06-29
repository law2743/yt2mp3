from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from app.models.rhythm import VocalOnsetEvent

ALGORITHM_VERSION = "librosa-vocal-onset-v1"
HOP_LENGTH = 512


def analyze_vocal_onsets(
    source: Path,
    *,
    fallback_source: Path | None = None,
    min_separation_sec: float = 0.08,
    backtrack: bool = True,
) -> list[VocalOnsetEvent]:
    warnings: list[str] = []

    primary, primary_warning = _analyze_source(
        source,
        min_separation_sec=min_separation_sec,
        backtrack=backtrack,
        warnings=[],
    )
    if primary is not None:
        return primary

    warnings.append(primary_warning)
    if fallback_source is not None:
        fallback, fallback_warning = _analyze_source(
            fallback_source,
            min_separation_sec=min_separation_sec,
            backtrack=backtrack,
            warnings=[
                *warnings,
                "used_fallback_source",
                "fallback_source_may_include_accompaniment",
            ],
        )
        if fallback is not None:
            return fallback
        warnings.append(fallback_warning.replace("source_", "fallback_", 1))

    return []


def write_vocal_onsets_csv(
    onsets: list[VocalOnsetEvent],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "onset_id",
        "time_sec",
        "raw_score",
        "backtracked_time_sec",
        "source_backend",
        "is_primary",
        "voicing_support",
        "pitch_jump_cents",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for onset in onsets:
            writer.writerow(
                {
                    "onset_id": onset.onset_id,
                    "time_sec": _format_optional_float(onset.time_sec),
                    "raw_score": _format_optional_float(onset.raw_score),
                    "backtracked_time_sec": _format_optional_float(onset.backtracked_time_sec),
                    "source_backend": onset.source_backend,
                    "is_primary": onset.is_primary,
                    "voicing_support": "",
                    "pitch_jump_cents": "",
                }
            )


def _analyze_source(
    source: Path,
    *,
    min_separation_sec: float,
    backtrack: bool,
    warnings: list[str],
) -> tuple[list[VocalOnsetEvent] | None, str]:
    if not source.exists():
        return None, "source_missing"

    try:
        librosa = _librosa()
        y, sample_rate = librosa.load(source, sr=None, mono=True)
        onset_envelope = librosa.onset.onset_strength(
            y=y,
            sr=sample_rate,
            hop_length=HOP_LENGTH,
        )
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_envelope,
            sr=sample_rate,
            hop_length=HOP_LENGTH,
            units="frames",
            backtrack=False,
            pre_max=3,
            post_max=3,
            pre_avg=8,
            post_avg=8,
            delta=0.25,
            wait=_wait_frames(min_separation_sec, sample_rate),
        )
        raw_scores = _normalized_frame_scores(onset_envelope, onset_frames)
        selected = _dedupe_onsets(
            list(zip(onset_frames, raw_scores, strict=True)),
            min_separation_sec=min_separation_sec,
            sample_rate=sample_rate,
        )
        times = librosa.frames_to_time(
            [frame for frame, _score in selected],
            sr=sample_rate,
            hop_length=HOP_LENGTH,
        )
        backtracked_times = _backtracked_times(
            librosa,
            onset_envelope,
            selected,
            sample_rate,
            backtrack,
        )
    except Exception as exc:
        return None, f"source_analysis_failed:{type(exc).__name__}"

    return (
        [
            VocalOnsetEvent(
                onset_id=f"onset-{index + 1:04d}",
                time_sec=float(time_sec),
                confidence=float(score),
                raw_score=float(score),
                backtracked_time_sec=backtracked_times[index],
                source_backend="librosa",
                is_primary=True,
                strength=float(score),
                source_audio_path=str(source),
                warnings=warnings,
            )
            for index, (time_sec, (_frame, score)) in enumerate(zip(times, selected, strict=True))
        ],
        "",
    )


def _dedupe_onsets(
    candidates: list[tuple[int, float]],
    *,
    min_separation_sec: float,
    sample_rate: int,
) -> list[tuple[int, float]]:
    if not candidates:
        return []

    min_separation_frames = max(1, round(min_separation_sec * sample_rate / HOP_LENGTH))
    selected: list[tuple[int, float]] = []
    for frame, score in sorted(candidates, key=lambda item: item[0]):
        if not selected or frame - selected[-1][0] >= min_separation_frames:
            selected.append((int(frame), float(score)))
            continue

        # For close duplicate peaks, keep the stronger onset candidate.
        if score > selected[-1][1]:
            selected[-1] = (int(frame), float(score))

    return selected


def _backtracked_times(
    librosa: Any,
    onset_envelope: Any,
    selected: list[tuple[int, float]],
    sample_rate: int,
    backtrack: bool,
) -> list[float | None]:
    if not backtrack or not selected:
        return [None for _frame, _score in selected]

    import numpy as np

    frames = np.array([frame for frame, _score in selected], dtype=np.int64)
    try:
        backtracked_frames = librosa.onset.onset_backtrack(frames, onset_envelope)
        times = librosa.frames_to_time(backtracked_frames, sr=sample_rate, hop_length=HOP_LENGTH)
    except Exception:
        return [None for _frame, _score in selected]
    return [float(time_sec) for time_sec in times]


def _normalized_frame_scores(onset_envelope: Any, onset_frames: Any) -> list[float]:
    if len(onset_frames) == 0:
        return []

    max_score = float(onset_envelope.max()) if len(onset_envelope) else 0.0
    if max_score <= 0:
        return [0.0 for _frame in onset_frames]

    scores: list[float] = []
    for frame in onset_frames:
        frame_index = int(frame)
        if frame_index < 0 or frame_index >= len(onset_envelope):
            scores.append(0.0)
            continue
        scores.append(max(0.0, min(1.0, float(onset_envelope[frame_index]) / max_score)))
    return scores


def _wait_frames(min_separation_sec: float, sample_rate: int) -> int:
    return max(1, round(min_separation_sec * sample_rate / HOP_LENGTH))


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def _librosa():
    os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/yt2mp3-numba-cache")
    import librosa

    return librosa
