import numpy as np
import pytest
import soundfile as sf

from app.config import Settings
from app.models import JobStatus, KeyAnalysisResult, KeyCandidate
from app.models.melody import MelodyAnalysisResult
from app.services.job_manager import JobManager
from app.services.melody import analyze_melody
from app.services.pipelines.melody import MelodyPipeline
from app.services.youtube import canonicalize_youtube_url

SAMPLE_RATE = 22050


def _tone(midi_note, duration, vibrato_semitones=0):
    time = np.arange(round(SAMPLE_RATE * duration)) / SAMPLE_RATE
    frequency = 440 * 2 ** ((midi_note - 69) / 12)
    if vibrato_semitones:
        frequency = frequency * 2 ** (
            (vibrato_semitones * np.sin(2 * np.pi * 5 * time)) / 12
        )
        phase = 2 * np.pi * np.cumsum(frequency) / SAMPLE_RATE
        return 0.3 * np.sin(phase)
    return 0.3 * np.sin(2 * np.pi * frequency * time)


def _analyze(tmp_path, audio, name="fixture"):
    source = tmp_path / f"{name}.wav"
    json_path = tmp_path / f"{name}.json"
    midi_path = tmp_path / f"{name}.mid"
    sf.write(source, audio, SAMPLE_RATE)
    analyze_melody(
        source,
        json_path,
        midi_path,
        job_id=name,
        key="C Major",
        root_index=0,
        mode="major",
        meter_hint="auto",
        min_note_duration_sec=0.12,
        max_gap_merge_sec=0.08,
        min_confidence=0.45,
        fmin="C2",
        fmax="C6",
        max_notes=2000,
    )
    return MelodyAnalysisResult.model_validate_json(json_path.read_text()), midi_path


@pytest.mark.parametrize(
    ("name", "audio", "maximum_notes"),
    [
        ("silence", np.zeros(SAMPLE_RATE), 0),
        ("white-noise", np.random.default_rng(7).normal(0, 0.02, SAMPLE_RATE), 2),
        ("vibrato", _tone(69, 1.2, 0.25), 3),
    ],
)
def test_silence_noise_and_vibrato_are_not_fragmented(tmp_path, name, audio, maximum_notes):
    result, midi_path = _analyze(tmp_path, audio, name)
    assert result.summary.note_count <= maximum_notes
    assert midi_path.stat().st_size > 0


def test_a4_and_c_major_scale_pitch_mapping(tmp_path):
    a4, _ = _analyze(tmp_path, _tone(69, 1), "a4")
    assert a4.notes
    assert a4.notes[0].midi_note == 69

    silence = np.zeros(round(SAMPLE_RATE * 0.08))
    scale_audio = np.concatenate(
        [part for midi in (60, 62, 64, 65, 67, 69, 71, 72) for part in (_tone(midi, 0.3), silence)]
    )
    scale, _ = _analyze(tmp_path, scale_audio, "c-major-scale")
    detected = [note.midi_note for note in scale.notes]
    expected = [60, 62, 64, 65, 67, 69, 71, 72]
    assert all(note in detected for note in expected)


@pytest.mark.asyncio
async def test_melody_pipeline_reuses_analysis_audio_and_exports_atomically(tmp_path):
    settings = Settings(app_env="test", work_root=tmp_path, melody_timeout_seconds=60)
    manager = JobManager(settings)
    job = await manager.create(
        "owner", canonicalize_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    )
    queued = await manager.queue.get()
    assert queued.operation == "analyze"
    manager.queue.task_done()
    sf.write(job.artifacts.analysis_audio, _tone(69, 1), SAMPLE_RATE)
    job.status = JobStatus.READY
    job.analysis = KeyAnalysisResult(
        root_index=0,
        root_name="C",
        mode="major",
        display_name="C Major",
        confidence=0.8,
        candidates=[KeyCandidate(key="C Major", score=1)],
        algorithm_version="fixture",
    )
    result = await MelodyPipeline(settings).run(job, "6/8")
    assert result.notes[0].midi_note == 69
    assert result.meter_used == "6/8"
    assert job.artifacts.melody_json.exists()
    assert job.artifacts.melody_midi.read_bytes().startswith(b"MThd")
