import math

import numpy as np

from app.models.melody import MelodyAnalysisResult, MelodyNote, MelodySummary
from app.services.melody import (
    _note_name,
    _numbered_notation,
    _write_midi,
    build_notation_lines,
    segment_pitch_frames,
)


def test_note_name_and_numbered_notation_mapping():
    assert _note_name(69) == "A4"
    assert _numbered_notation(60, 0) == (1, "1")
    assert _numbered_notation(72, 0) == (1, "1'")
    assert _numbered_notation(48, 0) == (1, "1,")
    assert _numbered_notation(66, 0) == (4, "#4")
    # C is the natural-minor third when the tonic is A.
    assert _numbered_notation(60, 9) == (3, "b3,")


def test_pitch_segmentation_filters_confidence_and_merges_short_gap():
    f0 = np.array([440.0, math.nan, 440.0, 466.16, 440.0])
    voiced = np.array([True, False, True, True, True])
    confidence = np.array([0.9, 0.0, 0.8, 0.3, 0.2])
    segments, valid = segment_pitch_frames(
        f0,
        voiced,
        confidence,
        frame_seconds=0.02,
        min_confidence=0.45,
        max_gap_merge_sec=0.03,
    )
    assert valid.tolist() == [True, False, True, False, False]
    assert len(segments) == 1
    assert segments[0][:3] == (0, 2, 69)


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


def test_preview_adds_only_clear_gap_rest_and_meter_separator():
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
    assert build_notation_lines(_result(notes, "4/4")) == ["1 - - - - | 3"]


def test_melody_schema_and_empty_midi_are_serializable(tmp_path):
    result = _result([])
    restored = MelodyAnalysisResult.model_validate_json(result.model_dump_json())
    assert restored.summary.note_count == 0

    path = tmp_path / "melody.mid"
    _write_midi(path, [], None)
    assert path.read_bytes().startswith(b"MThd")
