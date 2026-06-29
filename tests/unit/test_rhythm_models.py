from app.models.rhythm import (
    BeatEvent,
    BeatGridResult,
    MeterHypothesis,
    NoteDraft,
    NoteDraftResult,
    RhythmDiagnostics,
    VocalOnsetEvent,
)


def test_beat_grid_result_can_be_created_and_serialized():
    result = BeatGridResult(
        algorithm_version="contract-v1",
        duration_seconds=8.0,
        bpm=120.0,
        meter="4/4",
        beats_per_bar=4,
        beats=[
            BeatEvent(
                beat_index=0,
                time_sec=0.0,
                beat_in_bar=1,
                bar_index=0,
                tempo_bpm=120.0,
                confidence=0.95,
            )
        ],
        meter_hypotheses=[
            MeterHypothesis(meter="4/4", confidence=0.9, bpm=120.0, beats_per_bar=4)
        ],
    )

    payload = result.model_dump()
    assert payload["schema_version"] == "rhythm.beat_grid.v1"
    assert payload["source_audio_path"] == "analysis/stems/accompaniment.wav"
    assert payload["beats"][0]["time_sec"] == 0.0
    assert result.model_validate_json(result.model_dump_json()) == result


def test_vocal_onset_event_can_be_created_and_serialized():
    onset = VocalOnsetEvent(onset_id="onset-1", time_sec=1.25, confidence=0.82, strength=3.5)

    payload = onset.model_dump()
    assert payload == {
        "onset_id": "onset-1",
        "time_sec": 1.25,
        "confidence": 0.82,
        "raw_score": 0,
        "backtracked_time_sec": None,
        "source_backend": "librosa",
        "is_primary": True,
        "strength": 3.5,
        "source_audio_path": "analysis/stems/vocals.wav",
        "warnings": [],
    }
    assert VocalOnsetEvent.model_validate_json(onset.model_dump_json()) == onset


def test_note_draft_core_fields_align_with_melody_note_semantics():
    note = NoteDraft(
        note_id="note-1",
        start_sec=1.0,
        end_sec=1.5,
        duration_sec=0.5,
        midi_note=64,
        frequency_hz=329.63,
        raw_beat_start=4.1,
        raw_beat_duration=0.9,
        quantized_beat_start=4.0,
        quantized_beat_duration=1.0,
        bar_index=1,
        scale_degree=3,
        numbered_notation="3",
        pitch_confidence=0.91,
        onset_confidence=0.78,
        quantization_confidence=0.86,
        boundary_source="hybrid",
    )

    payload = note.model_dump()
    for field in (
        "start_sec",
        "end_sec",
        "duration_sec",
        "midi_note",
        "frequency_hz",
        "quantized_beat_start",
        "quantized_beat_duration",
        "bar_index",
        "scale_degree",
        "numbered_notation",
    ):
        assert field in payload
    assert payload["raw_beat_start"] == 4.1
    assert payload["pitch_source"] == "hybrid_postprocessed"
    assert NoteDraft.model_validate_json(note.model_dump_json()) == note


def test_note_draft_result_can_contain_multiple_notes():
    notes = [
        NoteDraft(
            note_id="note-1",
            start_sec=0.0,
            end_sec=0.5,
            duration_sec=0.5,
            midi_note=60,
        ),
        NoteDraft(
            note_id="note-2",
            start_sec=0.5,
            end_sec=1.0,
            duration_sec=0.5,
            midi_note=62,
        ),
    ]

    result = NoteDraftResult(algorithm_version="contract-v1", notes=notes)

    assert len(result.notes) == 2
    assert result.model_dump()["notes"][1]["midi_note"] == 62
    assert NoteDraftResult.model_validate_json(result.model_dump_json()) == result


def test_rhythm_diagnostics_can_contain_meter_hypotheses_and_warnings():
    diagnostics = RhythmDiagnostics(
        algorithm_version="contract-v1",
        meter_hypotheses=[
            MeterHypothesis(meter="4/4", confidence=0.72, bpm=98.0, beats_per_bar=4),
            MeterHypothesis(meter="3/4", confidence=0.18, bpm=98.0, beats_per_bar=3),
        ],
        warnings=["meter confidence below production threshold"],
    )

    payload = diagnostics.model_dump()
    assert payload["schema_version"] == "rhythm.diagnostics.v1"
    assert [item["meter"] for item in payload["meter_hypotheses"]] == ["4/4", "3/4"]
    assert payload["warnings"] == ["meter confidence below production threshold"]
    assert RhythmDiagnostics.model_validate_json(diagnostics.model_dump_json()) == diagnostics
