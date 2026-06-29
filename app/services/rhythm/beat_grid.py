from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.models.melody import MeterUsed
from app.models.rhythm import BeatEvent, BeatGridResult

ALGORITHM_VERSION = "librosa-beat-grid-v1"
SUPPORTED_METER_HINTS: set[str] = {"auto", "none", "4/4", "3/4", "6/8"}


def analyze_beat_grid(
    source: Path,
    *,
    meter_hint: str = "auto",
    fallback_source: Path | None = None,
) -> BeatGridResult:
    warnings: list[str] = []
    normalized_hint = meter_hint if meter_hint in SUPPORTED_METER_HINTS else "auto"
    if normalized_hint != meter_hint:
        warnings.append(f"unsupported_meter_hint:{meter_hint}")

    primary, primary_warning = _analyze_source(source, normalized_hint)
    if primary is not None:
        primary.warnings = [*warnings, *primary.warnings]
        return primary

    warnings.append(primary_warning)
    if fallback_source is not None:
        fallback, fallback_warning = _analyze_source(fallback_source, normalized_hint)
        if fallback is not None:
            fallback.warnings = [*warnings, "used_fallback_source", *fallback.warnings]
            return fallback
        warnings.append(fallback_warning.replace("source_", "fallback_", 1))
    else:
        warnings.append("fallback_source_not_provided")

    return _empty_result(source, normalized_hint, warnings)


def _analyze_source(source: Path, meter_hint: str) -> tuple[BeatGridResult | None, str]:
    if not source.exists():
        return None, "source_missing"

    try:
        librosa = _librosa()
        hop_length = 512
        y, sample_rate = librosa.load(source, sr=None, mono=True)
        duration_seconds = float(librosa.get_duration(y=y, sr=sample_rate))
        onset_envelope = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop_length)
        tempo_raw, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_envelope,
            sr=sample_rate,
            hop_length=hop_length,
        )
        tempo = _coerce_optional_bpm(tempo_raw)
        beat_times = [
            float(time)
            for time in librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)
        ]
    except Exception as exc:
        return None, f"source_analysis_failed:{type(exc).__name__}"

    return (
        _build_result(
            source_audio_path=str(source),
            duration_seconds=duration_seconds,
            tempo_bpm=tempo,
            beat_times=beat_times,
            meter_hint=meter_hint,
        ),
        "",
    )


def _build_result(
    *,
    source_audio_path: str,
    duration_seconds: float,
    tempo_bpm: int | None,
    beat_times: list[float],
    meter_hint: str,
) -> BeatGridResult:
    meter_used, beats_per_bar = _meter_settings(meter_hint)
    pulse_unit = "dotted_quarter" if meter_hint == "6/8" else None
    subdivision_unit = "eighth" if meter_hint == "6/8" else None
    subdivisions_per_beat = 3 if meter_hint == "6/8" else None
    bar_starts = _bar_starts(beat_times, beats_per_bar)
    warnings = ["auto_meter_not_implemented"] if meter_hint == "auto" else []

    beats = [
        _beat_event(
            beat_index=index,
            time_sec=time_sec,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )
        for index, time_sec in enumerate(beat_times)
    ]

    return BeatGridResult(
        algorithm_version=ALGORITHM_VERSION,
        source_audio_path=source_audio_path,
        duration_seconds=duration_seconds,
        bpm=tempo_bpm,
        meter=meter_used,
        meter_used=meter_used,
        beats_per_bar=beats_per_bar,
        pulse_unit=pulse_unit,
        subdivision_unit=subdivision_unit,
        subdivisions_per_beat=subdivisions_per_beat,
        beat_times_sec=beat_times,
        bar_starts_sec=bar_starts,
        beats=beats,
        warnings=warnings,
    )


def _beat_event(
    *,
    beat_index: int,
    time_sec: float,
    tempo_bpm: int | None,
    beats_per_bar: int | None,
) -> BeatEvent:
    if beats_per_bar is None:
        return BeatEvent(beat_index=beat_index, time_sec=time_sec, tempo_bpm=tempo_bpm)
    return BeatEvent(
        beat_index=beat_index,
        time_sec=time_sec,
        beat_in_bar=(beat_index % beats_per_bar) + 1,
        bar_index=beat_index // beats_per_bar,
        tempo_bpm=tempo_bpm,
    )


def _meter_settings(meter_hint: str) -> tuple[MeterUsed, int | None]:
    if meter_hint == "4/4":
        return "4/4", 4
    if meter_hint == "3/4":
        return "3/4", 3
    if meter_hint == "6/8":
        return "6/8", 2
    return "none", None


def _bar_starts(beat_times: list[float], beats_per_bar: int | None) -> list[float]:
    if beats_per_bar is None:
        return []
    return [time_sec for index, time_sec in enumerate(beat_times) if index % beats_per_bar == 0]


def _empty_result(source: Path, meter_hint: str, warnings: list[str]) -> BeatGridResult:
    meter_used, beats_per_bar = _meter_settings(meter_hint)
    if meter_hint == "auto":
        warnings = [*warnings, "auto_meter_not_implemented"]
    return BeatGridResult(
        algorithm_version=ALGORITHM_VERSION,
        source_audio_path=str(source),
        duration_seconds=0,
        bpm=None,
        meter=meter_used,
        meter_used=meter_used,
        beats_per_bar=beats_per_bar,
        pulse_unit="dotted_quarter" if meter_hint == "6/8" else None,
        subdivision_unit="eighth" if meter_hint == "6/8" else None,
        subdivisions_per_beat=3 if meter_hint == "6/8" else None,
        beat_times_sec=[],
        bar_starts_sec=[],
        beats=[],
        warnings=warnings,
    )


def _coerce_optional_bpm(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if hasattr(value, "item"):
            value = value.item()
        elif isinstance(value, (list, tuple)) and value:
            value = value[0]
        tempo = float(value)
    except (TypeError, ValueError):
        return None
    if tempo <= 0:
        return None
    return max(1, int(round(tempo)))


def _librosa():
    os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/yt2mp3-numba-cache")
    import librosa

    return librosa
