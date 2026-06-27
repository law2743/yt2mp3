from __future__ import annotations

import math
from statistics import median

A4_HZ = 440.0
A4_MIDI = 69.0


def hz_to_midi(f0_hz: float) -> float:
    if f0_hz <= 0:
        return 0.0
    return A4_MIDI + 12.0 * math.log2(f0_hz / A4_HZ)


def midi_to_hz(midi: float) -> float:
    return A4_HZ * (2.0 ** ((midi - A4_MIDI) / 12.0))


def hz_to_cents(f0_hz: float) -> float:
    return 1200.0 * math.log2(max(f0_hz, 1e-9))


def cents_distance(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0:
        return float("inf")
    return abs(1200.0 * math.log2(a_hz / b_hz))


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except Exception:
        return default


def safe_bool(value: object) -> bool:
    return bool(value)


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * min(max(q, 0.0), 1.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def robust_normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5 if value > 0 else 0.0
    return min(max((value - low) / (high - low), 0.0), 1.0)


def median_step(times: list[float], fallback: float = 0.01) -> float:
    if len(times) < 2:
        return fallback
    diffs = [round(times[i + 1] - times[i], 6) for i in range(min(len(times) - 1, 1000))]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return fallback
    return float(median(diffs))


def round_time(t: float) -> float:
    # The input pitch CSVs use 10 ms resolution. Rounding avoids 1.7200000002 keys.
    return round(t + 1e-9, 6)


def estimate_note_segments(times: list[float], f0s: list[float], voiced: list[bool], *, cents_merge: float = 60.0) -> list[dict[str, object]]:
    """Create coarse note-like segments for diagnostics and later melody JSON conversion.

    This is intentionally conservative. It is not full rhythm quantization.
    """
    if not times:
        return []
    segments: list[dict[str, object]] = []
    start = 0
    last_voiced = voiced[0]
    last_f0 = f0s[0]
    for i in range(1, len(times)):
        same_state = voiced[i] == last_voiced
        same_pitch = True
        if voiced[i] and last_voiced:
            same_pitch = cents_distance(max(f0s[i], 1e-9), max(last_f0, 1e-9)) <= cents_merge
        if not same_state or not same_pitch:
            segments.append(_segment_from_slice(times, f0s, voiced, start, i))
            start = i
            last_voiced = voiced[i]
            last_f0 = f0s[i]
        elif voiced[i]:
            # Track a slowly moving representative pitch to tolerate vibrato.
            last_f0 = (last_f0 * 0.8) + (f0s[i] * 0.2)
    segments.append(_segment_from_slice(times, f0s, voiced, start, len(times)))
    return segments


def _segment_from_slice(times: list[float], f0s: list[float], voiced: list[bool], start: int, end: int) -> dict[str, object]:
    end_index = max(end - 1, start)
    step = times[1] - times[0] if len(times) > 1 else 0.01
    f0_values = [f for f, v in zip(f0s[start:end], voiced[start:end]) if v and f > 0]
    median_f0 = median(f0_values) if f0_values else 0.0
    return {
        "start_sec": round(times[start], 6),
        "end_sec": round(times[end_index] + step, 6),
        "duration_sec": round(times[end_index] + step - times[start], 6),
        "voiced": bool(any(voiced[start:end])),
        "f0_hz": round(float(median_f0), 6),
        "midi": round(hz_to_midi(float(median_f0)), 4) if median_f0 > 0 else 0.0,
    }
