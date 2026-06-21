import json

import librosa
import numpy as np
import pytest
import soundfile as sf

from app.config import Settings
from app.services.audio import transpose_audio
from app.services.process import run_process


@pytest.mark.asyncio
async def test_pitch_shift_frequency_duration_and_metadata(tmp_path):
    sample_rate = 44100
    duration = 3.0
    time = np.arange(int(sample_rate * duration)) / sample_rate
    source = tmp_path / "source.wav"
    sf.write(source, np.sin(2 * np.pi * 440 * time) * 0.3, sample_rate)
    settings = Settings(app_env="test", work_root=tmp_path, transpose_timeout_seconds=60)

    output = await transpose_audio(
        source,
        tmp_path,
        1,
        "A440 fixture",
        "Tests",
        "Bb Major",
        settings,
        bitrate_kbps=192,
    )
    audio, rate = librosa.load(output, sr=sample_rate, mono=True)
    spectrum = np.abs(np.fft.rfft(audio))
    frequencies = np.fft.rfftfreq(audio.size, 1 / rate)
    dominant = frequencies[int(np.argmax(spectrum))]

    assert dominant == pytest.approx(466.16, rel=0.005)
    assert len(audio) / rate == pytest.approx(duration, abs=0.1)

    probe = await run_process(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(output)],
        timeout=10,
    )
    tags = json.loads(probe.stdout)["format"]["tags"]
    normalized = {key.lower(): value for key, value in tags.items()}
    assert normalized["title"] == "A440 fixture [升1半音・Bb Major]"
    assert normalized["artist"] == "Tests"

    assert output.name == "shift_1_192k.mp3"
