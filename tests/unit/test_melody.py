import json

import numpy as np
import soundfile as sf

from app.models.melody import MelodyAnalysisResult, MelodyNote, MelodySummary
from app.services.melody import (
    _auto_meter_metadata,
    _note_name,
    _numbered_notation,
    _write_midi,
    analyze_rmvpe_melody,
    build_notation_lines,
)


def test_note_name_and_numbered_notation_mapping():
    assert _note_name(69) == "A4"
    assert _numbered_notation(60, 0) == (1, "1")
    assert _numbered_notation(72, 0) == (1, "1'")
    assert _numbered_notation(48, 0) == (1, "1,")
    assert _numbered_notation(66, 0) == (4, "#4")
    # C is the natural-minor third when the tonic is A.
    assert _numbered_notation(60, 9) == (3, "b3,")


def test_auto_meter_does_not_guess_silent_audio():
    sample_rate = 22050
    hop_length = 512
    beat_frames = np.arange(16)
    y = np.zeros(4096, dtype=np.float32)

    meter, signature = _auto_meter_metadata(y, sample_rate, hop_length, beat_frames)

    assert (meter, signature) == ("none", None)


def _result(notes, meter_used="none"):
    return MelodyAnalysisResult(
        job_id="fixture",
        key="C Major",
        mode="major",
        meter_hint=meter_used,
        meter_used=meter_used,
        time_signature=meter_used if meter_used != "none" else None,
        notes=notes,
        summary=MelodySummary(
            note_count=len(notes), voiced_ratio=0.5, average_confidence=0.8
        ),
    )


def test_preview_uses_four_bars_per_line():
    notes = [
        MelodyNote(
            note_id="n0001",
            start_sec=0,
            end_sec=0.4,
            duration_sec=0.4,
            midi_note=60,
            note_name="C4",
            octave=4,
            beat_start=0,
            beat_duration=0.5,
            quantized_beat_start=0,
            quantized_beat_duration=0.5,
            bar_index=0,
            scale_degree=1,
            numbered_notation="1",
            confidence=0.8,
        ),
        MelodyNote(
            note_id="n0002",
            start_sec=1.5,
            end_sec=1.9,
            duration_sec=0.4,
            midi_note=64,
            note_name="E4",
            octave=4,
            beat_start=4,
            beat_duration=0.5,
            quantized_beat_start=4,
            quantized_beat_duration=0.5,
            bar_index=1,
            scale_degree=3,
            numbered_notation="3",
            confidence=0.8,
        ),
    ]
    assert build_notation_lines(_result(notes, "4/4")) == ["| 1 | 3 | - | - |"]


def test_melody_schema_and_empty_midi_are_serializable(tmp_path):
    result = _result([])
    restored = MelodyAnalysisResult.model_validate_json(result.model_dump_json())
    assert restored.summary.note_count == 0

    path = tmp_path / "melody.mid"
    _write_midi(path, [], None)
    assert path.read_bytes().startswith(b"MThd")
    import mido

    midi = mido.MidiFile(path)
    assert midi.type == 0
    assert len(midi.tracks) == 1


def test_rmvpe_pitch_points_generate_melody_json_and_midi(tmp_path):
    source = tmp_path / "vocals.wav"
    sample_rate = 16000
    sf.write(source, np.zeros(sample_rate, dtype=np.float32), sample_rate)
    pitch_json = tmp_path / "vocal_pitch.json"
    points = [
        {
            "time": round(index * 0.01, 2),
            "frequency_hz": 440.0,
            "midi": 69.0,
            "confidence": 0.9,
            "voiced": True,
        }
        for index in range(30)
    ]
    pitch_json.write_text(
        json.dumps(
            {
                "schema_version": "vocal_pitch.v1",
                "backend": "rmvpe_onnx",
                "fallback_used": False,
                "input_source": "vocals",
                "sample_rate": sample_rate,
                "duration_seconds": 1.0,
                "frame_hz": 100,
                "hop_seconds": 0.01,
                "voiced_confidence_threshold": 0.03,
                "points": points,
                "metadata": {
                    "model": "rmvpe-onnx",
                    "device": "cuda",
                    "confidence_source": "rmvpe_onnx",
                    "created_at": "2026-06-25T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "melody.json"
    midi = tmp_path / "melody.mid"

    analyze_rmvpe_melody(
        source,
        pitch_json,
        output,
        midi,
        job_id="fixture",
        key="C Major",
        root_index=0,
        mode="major",
        meter_hint="none",
        min_note_duration_sec=0.12,
        max_gap_merge_sec=0.08,
        min_confidence=0.45,
        max_notes=2000,
        beat_reference=source,
    )

    result = MelodyAnalysisResult.model_validate_json(output.read_text(encoding="utf-8"))
    assert result.pitch_backend == "rmvpe_onnx"
    assert result.is_fallback is False
    assert result.notes[0].source == "rmvpe_onnx"
    assert result.notes[0].midi_note == 69
    assert midi.read_bytes().startswith(b"MThd")
