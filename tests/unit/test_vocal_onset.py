from pathlib import Path

import csv

import numpy as np
import soundfile as sf

from app.models.rhythm import VocalOnsetEvent
from app.services.rhythm.vocal_onset import analyze_vocal_onsets, write_vocal_onsets_csv


def _write_bursts(
    path: Path,
    *,
    starts: list[float],
    amplitudes: list[float] | None = None,
    sample_rate: int = 22050,
) -> None:
    duration = max(starts) + 0.5
    audio = np.zeros(int(duration * sample_rate), dtype=np.float32)
    burst_length = int(0.04 * sample_rate)
    envelope = np.hanning(burst_length).astype(np.float32)
    tone = np.sin(2 * np.pi * 440 * np.arange(burst_length) / sample_rate).astype(np.float32)
    burst = envelope * tone
    amplitudes = amplitudes or [1.0 for _start in starts]

    for start_sec, amplitude in zip(starts, amplitudes, strict=True):
        start = int(start_sec * sample_rate)
        audio[start : start + burst_length] += burst * amplitude

    sf.write(path, audio, sample_rate)


def test_synthetic_short_events_produce_vocal_onsets(tmp_path):
    source = tmp_path / "vocals.wav"
    _write_bursts(source, starts=[0.35, 0.75, 1.15])

    onsets = analyze_vocal_onsets(source)

    assert len(onsets) >= 2
    assert all(isinstance(onset, VocalOnsetEvent) for onset in onsets)


def test_vocal_onsets_include_required_event_fields(tmp_path):
    source = tmp_path / "vocals.wav"
    _write_bursts(source, starts=[0.4, 0.9])

    onsets = analyze_vocal_onsets(source)

    assert onsets
    for onset in onsets:
        assert onset.onset_id.startswith("onset-")
        assert onset.time_sec >= 0
        assert 0 <= onset.raw_score <= 1
        assert onset.source_backend == "librosa"


def test_min_separation_removes_close_onsets(tmp_path):
    source = tmp_path / "vocals.wav"
    _write_bursts(source, starts=[0.40, 0.45, 0.90], amplitudes=[0.7, 1.0, 1.0])

    loose = analyze_vocal_onsets(source, min_separation_sec=0.01)
    strict = analyze_vocal_onsets(source, min_separation_sec=0.12)

    assert len(loose) >= len(strict)
    assert len(strict) <= 2


def test_backtrack_true_populates_backtracked_time(tmp_path):
    source = tmp_path / "vocals.wav"
    _write_bursts(source, starts=[0.5, 1.0])

    onsets = analyze_vocal_onsets(source, backtrack=True)

    assert onsets
    assert all(onset.backtracked_time_sec is not None for onset in onsets)
    assert all(onset.backtracked_time_sec <= onset.time_sec for onset in onsets)


def test_missing_source_uses_existing_fallback(tmp_path):
    source = tmp_path / "missing.wav"
    fallback = tmp_path / "mono-22050.wav"
    _write_bursts(fallback, starts=[0.3, 0.8])

    onsets = analyze_vocal_onsets(source, fallback_source=fallback)

    assert onsets
    assert all(onset.source_audio_path == str(fallback) for onset in onsets)
    assert all("used_fallback_source" in onset.warnings for onset in onsets)
    assert all("fallback_source_may_include_accompaniment" in onset.warnings for onset in onsets)


def test_missing_source_and_fallback_returns_empty_list(tmp_path):
    onsets = analyze_vocal_onsets(
        tmp_path / "missing.wav",
        fallback_source=tmp_path / "also-missing.wav",
    )

    assert onsets == []


def test_write_vocal_onsets_csv_outputs_expected_columns(tmp_path):
    output_path = tmp_path / "analysis" / "rhythm" / "vocal_onsets.csv"
    onsets = [
        VocalOnsetEvent(
            onset_id="onset-0001",
            time_sec=0.5,
            confidence=0.8,
            raw_score=0.8,
            backtracked_time_sec=0.48,
            source_backend="librosa",
            is_primary=True,
        )
    ]

    write_vocal_onsets_csv(onsets, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "onset_id": "onset-0001",
            "time_sec": "0.500000",
            "raw_score": "0.800000",
            "backtracked_time_sec": "0.480000",
            "source_backend": "librosa",
            "is_primary": "True",
            "voicing_support": "",
            "pitch_jump_cents": "",
        }
    ]
