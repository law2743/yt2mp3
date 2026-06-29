from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.rhythm.beat_grid import analyze_beat_grid
from app.services.rhythm.beat_grid import _coerce_optional_bpm


def _write_click_track(path: Path, *, bpm: float = 120.0, beats: int = 12, sample_rate: int = 22050) -> None:
    duration = (60.0 / bpm) * (beats + 2)
    audio = np.zeros(int(duration * sample_rate), dtype=np.float32)
    click_length = int(0.02 * sample_rate)
    click = np.hanning(click_length).astype(np.float32)
    for beat in range(beats):
        start = int((0.5 + beat * 60.0 / bpm) * sample_rate)
        audio[start : start + click_length] += click
    sf.write(path, audio, sample_rate)


def test_click_track_produces_beat_times(tmp_path):
    source = tmp_path / "clicks.wav"
    _write_click_track(source)

    result = analyze_beat_grid(source, meter_hint="auto")

    assert result.backend == "librosa"
    assert result.bpm is not None
    assert len(result.beat_times_sec) >= 6
    assert len(result.beats) == len(result.beat_times_sec)
    assert result.source_audio_path == str(source)


def test_detected_bpm_is_rounded_to_integer():
    assert _coerce_optional_bpm(109.956) == 110


def test_meter_hint_none_does_not_produce_bar_grid(tmp_path):
    source = tmp_path / "clicks.wav"
    _write_click_track(source)

    result = analyze_beat_grid(source, meter_hint="none")

    assert result.meter_used == "none"
    assert result.beats_per_bar is None
    assert result.bar_starts_sec == []
    assert all(beat.bar_index is None and beat.beat_in_bar is None for beat in result.beats)


def test_meter_hint_4_4_assigns_bar_index_every_four_beats(tmp_path):
    source = tmp_path / "clicks.wav"
    _write_click_track(source, beats=10)

    result = analyze_beat_grid(source, meter_hint="4/4")

    assert result.meter_used == "4/4"
    assert result.beats_per_bar == 4
    assert result.beats[0].bar_index == 0
    assert result.beats[0].beat_in_bar == 1
    assert result.beats[4].bar_index == 1
    assert result.beats[4].beat_in_bar == 1
    assert result.bar_starts_sec == result.beat_times_sec[::4]


def test_meter_hint_3_4_assigns_bar_index_every_three_beats(tmp_path):
    source = tmp_path / "clicks.wav"
    _write_click_track(source, beats=10)

    result = analyze_beat_grid(source, meter_hint="3/4")

    assert result.meter_used == "3/4"
    assert result.beats_per_bar == 3
    assert result.beats[0].bar_index == 0
    assert result.beats[3].bar_index == 1
    assert result.beats[3].beat_in_bar == 1
    assert result.bar_starts_sec == result.beat_times_sec[::3]


def test_meter_hint_6_8_sets_pulse_and_subdivision_metadata(tmp_path):
    source = tmp_path / "clicks.wav"
    _write_click_track(source, beats=8)

    result = analyze_beat_grid(source, meter_hint="6/8")

    assert result.meter_used == "6/8"
    assert result.beats_per_bar == 2
    assert result.pulse_unit == "dotted_quarter"
    assert result.subdivision_unit == "eighth"
    assert result.subdivisions_per_beat == 3
    assert result.beats[2].bar_index == 1
    assert result.bar_starts_sec == result.beat_times_sec[::2]


def test_missing_source_uses_existing_fallback(tmp_path):
    source = tmp_path / "missing.wav"
    fallback = tmp_path / "fallback.wav"
    _write_click_track(fallback)

    result = analyze_beat_grid(source, meter_hint="4/4", fallback_source=fallback)

    assert result.source_audio_path == str(fallback)
    assert "source_missing" in result.warnings
    assert "used_fallback_source" in result.warnings
    assert len(result.beat_times_sec) > 0


def test_missing_source_and_fallback_returns_empty_result(tmp_path):
    result = analyze_beat_grid(
        tmp_path / "missing.wav",
        meter_hint="auto",
        fallback_source=tmp_path / "fallback-missing.wav",
    )

    assert result.backend == "librosa"
    assert result.beat_times_sec == []
    assert result.beats == []
    assert "source_missing" in result.warnings
    assert "fallback_missing" in result.warnings
    assert "auto_meter_not_implemented" in result.warnings
