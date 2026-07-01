from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.models.melody import MeterUsed
from app.models.rhythm import BeatEvent, BeatGridResult, MeterHypothesis

ALGORITHM_VERSION = "librosa-beat-grid-v1"
SUPPORTED_METER_HINTS: set[str] = {"auto", "none", "4/4", "3/4", "6/8"}
AUTO_METER_MIN_BEATS = 8
AUTO_METER_MIN_MARGIN = 0.08
AUTO_METER_MIN_SCORE = 0.05


class _AutoMeterCandidate:
    def __init__(
        self,
        *,
        meter: MeterUsed,
        beats_per_bar: int,
        offset: int,
        score: float,
        bar_accent: float,
        non_bar_accent: float,
        stability: float,
    ) -> None:
        self.meter = meter
        self.beats_per_bar = beats_per_bar
        self.offset = offset
        self.score = score
        self.bar_accent = bar_accent
        self.non_bar_accent = non_bar_accent
        self.stability = stability


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
            onset_envelope=[float(value) for value in onset_envelope],
            sample_rate=sample_rate,
            hop_length=hop_length,
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
    onset_envelope: list[float] | None = None,
    sample_rate: int | None = None,
    hop_length: int | None = None,
    meter_hint: str,
) -> BeatGridResult:
    meter_hypotheses: list[MeterHypothesis] = []
    phase_offset = 0
    warnings: list[str] = []
    if meter_hint == "auto":
        meter_used, beats_per_bar, phase_offset, meter_hypotheses, warnings = _auto_meter(
            beat_times=beat_times,
            onset_envelope=onset_envelope or [],
            sample_rate=sample_rate,
            hop_length=hop_length,
            tempo_bpm=tempo_bpm,
        )
    else:
        meter_used, beats_per_bar = _meter_settings(meter_hint)

    pulse_unit = "dotted_quarter" if meter_used == "6/8" else None
    subdivision_unit = "eighth" if meter_used == "6/8" else None
    subdivisions_per_beat = 3 if meter_used == "6/8" else None
    bar_starts = _bar_starts(beat_times, beats_per_bar, phase_offset)

    beats = [
        _beat_event(
            beat_index=index,
            time_sec=time_sec,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            phase_offset=phase_offset,
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
        meter_hypotheses=meter_hypotheses,
        warnings=warnings,
    )


def _beat_event(
    *,
    beat_index: int,
    time_sec: float,
    tempo_bpm: int | None,
    beats_per_bar: int | None,
    phase_offset: int = 0,
) -> BeatEvent:
    if beats_per_bar is None:
        return BeatEvent(beat_index=beat_index, time_sec=time_sec, tempo_bpm=tempo_bpm)
    relative_index = beat_index - phase_offset
    if relative_index < 0:
        return BeatEvent(beat_index=beat_index, time_sec=time_sec, tempo_bpm=tempo_bpm)
    return BeatEvent(
        beat_index=beat_index,
        time_sec=time_sec,
        beat_in_bar=(relative_index % beats_per_bar) + 1,
        bar_index=relative_index // beats_per_bar,
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


def _bar_starts(
    beat_times: list[float],
    beats_per_bar: int | None,
    phase_offset: int = 0,
) -> list[float]:
    if beats_per_bar is None:
        return []
    return [
        time_sec
        for index, time_sec in enumerate(beat_times)
        if index >= phase_offset and (index - phase_offset) % beats_per_bar == 0
    ]


def _auto_meter(
    *,
    beat_times: list[float],
    onset_envelope: list[float],
    sample_rate: int | None,
    hop_length: int | None,
    tempo_bpm: int | None,
) -> tuple[MeterUsed, int, int, list[MeterHypothesis], list[str]]:
    try:
        strengths = _beat_onset_strengths(
            beat_times=beat_times,
            onset_envelope=onset_envelope,
            sample_rate=sample_rate,
            hop_length=hop_length,
        )
        candidates = _score_auto_meter_candidates(strengths)
        hypotheses = _meter_hypotheses(candidates, tempo_bpm)
        fallback = (
            len(beat_times) < AUTO_METER_MIN_BEATS
            or not candidates
            or candidates[0].score < AUTO_METER_MIN_SCORE
            or (
                len(candidates) > 1
                and candidates[0].score - candidates[1].score < AUTO_METER_MIN_MARGIN
            )
        )
        if fallback:
            return (
                "4/4",
                4,
                0,
                hypotheses,
                ["auto_meter_low_confidence_fallback_4_4"],
            )
        selected = candidates[0]
        return selected.meter, selected.beats_per_bar, selected.offset, hypotheses, []
    except Exception:
        return "4/4", 4, 0, [], ["auto_meter_low_confidence_fallback_4_4"]


def _beat_onset_strengths(
    *,
    beat_times: list[float],
    onset_envelope: list[float],
    sample_rate: int | None,
    hop_length: int | None,
) -> list[float]:
    if not beat_times or not onset_envelope or not sample_rate or not hop_length:
        return [0.0 for _ in beat_times]
    strengths: list[float] = []
    last_index = len(onset_envelope) - 1
    for time_sec in beat_times:
        frame_index = int(round(time_sec * sample_rate / hop_length))
        left = max(0, frame_index - 1)
        right = min(last_index, frame_index + 1)
        strengths.append(max(float(onset_envelope[index]) for index in range(left, right + 1)))
    max_strength = max(strengths, default=0.0)
    if max_strength <= 0:
        return [0.0 for _ in strengths]
    return [strength / max_strength for strength in strengths]


def _score_auto_meter_candidates(strengths: list[float]) -> list[_AutoMeterCandidate]:
    specs: tuple[tuple[MeterUsed, int, tuple[int, ...]], ...] = (
        ("4/4", 4, (0, 1, 2, 3)),
        ("3/4", 3, (0, 1, 2)),
        ("6/8", 2, (0, 1)),
    )
    candidates: list[_AutoMeterCandidate] = []
    for meter, beats_per_bar, offsets in specs:
        best: _AutoMeterCandidate | None = None
        for offset in offsets:
            candidate = _score_candidate(strengths, meter, beats_per_bar, offset)
            if best is None or candidate.score > best.score:
                best = candidate
        if best is not None:
            candidates.append(best)
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def _score_candidate(
    strengths: list[float],
    meter: MeterUsed,
    beats_per_bar: int,
    offset: int,
) -> _AutoMeterCandidate:
    bar_values = [
        strength
        for index, strength in enumerate(strengths)
        if index >= offset and (index - offset) % beats_per_bar == 0
    ]
    non_bar_values = [
        strength
        for index, strength in enumerate(strengths)
        if index < offset or (index - offset) % beats_per_bar != 0
    ]
    bar_accent = _mean(bar_values)
    non_bar_accent = _mean(non_bar_values)
    stability = _stability(bar_values)
    score = (bar_accent - non_bar_accent) * (0.75 + 0.25 * stability)
    return _AutoMeterCandidate(
        meter=meter,
        beats_per_bar=beats_per_bar,
        offset=offset,
        score=score,
        bar_accent=bar_accent,
        non_bar_accent=non_bar_accent,
        stability=stability,
    )


def _meter_hypotheses(
    candidates: list[_AutoMeterCandidate],
    tempo_bpm: int | None,
) -> list[MeterHypothesis]:
    if not candidates:
        return []
    best_score = max(candidate.score for candidate in candidates)
    worst_score = min(candidate.score for candidate in candidates)
    score_span = max(best_score - worst_score, 1e-9)
    hypotheses: list[MeterHypothesis] = []
    for candidate in candidates:
        confidence = 1.0 if len(candidates) == 1 else (candidate.score - worst_score) / score_span
        hypotheses.append(
            MeterHypothesis(
                meter=candidate.meter,
                confidence=max(0.0, min(1.0, confidence)),
                score=round(candidate.score, 6),
                reason=(
                    f"offset={candidate.offset}; "
                    f"bar_accent={candidate.bar_accent:.3f}; "
                    f"non_bar_accent={candidate.non_bar_accent:.3f}; "
                    f"stability={candidate.stability:.3f}"
                ),
                bpm=tempo_bpm,
                beats_per_bar=candidate.beats_per_bar,
                source="auto_meter_heuristic",
            )
        )
    return hypotheses


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stability(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    if average <= 1e-9:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    coefficient_of_variation = (variance**0.5) / average
    return max(0.0, min(1.0, 1.0 - coefficient_of_variation))


def _empty_result(source: Path, meter_hint: str, warnings: list[str]) -> BeatGridResult:
    meter_used, beats_per_bar = _meter_settings(meter_hint)
    if meter_hint == "auto":
        meter_used = "4/4"
        beats_per_bar = 4
        warnings = [*warnings, "auto_meter_low_confidence_fallback_4_4"]
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
