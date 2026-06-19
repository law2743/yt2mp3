from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.key_analyzer import LibrosaKeyAnalyzer


def synth_chords(path: Path, roots: list[int], sample_rate: int = 22050) -> None:
    chunks = []
    for root in roots:
        time = np.arange(sample_rate * 2) / sample_rate
        chord = sum(
            np.sin(2 * np.pi * 261.6256 * 2 ** ((root + interval) / 12) * time)
            for interval in (0, 4, 7)
        ) / 3
        chunks.append(chord * np.hanning(chord.size) * 0.5)
    sf.write(path, np.concatenate(chunks), sample_rate)


def test_analyzer_detects_c_major_fixture(tmp_path):
    path = tmp_path / "c-major.wav"
    synth_chords(path, [0, 5, 7, 0])
    result = LibrosaKeyAnalyzer().analyze(path)
    assert result.display_name in {"C Major", "A Minor"}
    assert result.candidates[0].key == result.display_name


def test_analyzer_rejects_silence(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(22050 * 3), 22050)
    with pytest.raises(ValueError):
        LibrosaKeyAnalyzer().analyze(path)

