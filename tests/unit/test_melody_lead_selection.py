from __future__ import annotations

import csv
import json
from pathlib import Path

from app.services.artifacts import JobArtifacts
from app.services.melody_lead_selection import (
    build_lead_selection_diagnostics,
    build_bar_window_candidates,
    build_motif_families,
    build_motif_matches,
    build_phrase_candidates,
    build_similarity_matrix,
    build_structure_pattern_groups,
    filter_similarity_matrix_for_output,
    run_lead_selection_diagnostics,
)


def _beat_grid(*, bars: int = 8, beat_duration: float = 0.5) -> dict:
    beats_per_bar = 4
    beat_times = [index * beat_duration for index in range((bars + 1) * beats_per_bar + 1)]
    return {
        "bpm": 120,
        "meter_used": "4/4",
        "beats_per_bar": beats_per_bar,
        "beat_times_sec": beat_times,
        "bar_starts_sec": [
            index * beats_per_bar * beat_duration for index in range(bars + 1)
        ],
    }


def _note(index: int, start: float, midi: float, *, duration: float = 0.4) -> dict:
    return {
        "note_id": f"note-{index:04d}",
        "start_sec": start,
        "end_sec": start + duration,
        "duration_sec": duration,
        "midi_note": midi,
    }


def _melody_notes(
    pitches: list[int],
    *,
    start: float = 0.0,
    step: float = 0.5,
    duration: float = 0.36,
) -> list[dict]:
    return [_note(index + 1, start + index * step, pitch, duration=duration) for index, pitch in enumerate(pitches)]


def test_phrase_candidate_segmentation_splits_on_natural_gap():
    notes = [
        *_melody_notes([60, 62, 64], start=0.0),
        *_melody_notes([65, 67, 69], start=3.2),
    ]

    phrases = build_phrase_candidates(notes, _beat_grid())

    assert len(phrases) == 2
    assert phrases[0]["note_count"] == 3
    assert phrases[1]["start_sec"] == 3.2
    assert phrases[0]["start_bar"] == 0
    assert phrases[1]["end_bar"] is not None


def test_bar_window_generation_uses_sliding_multi_scale_windows():
    notes = _melody_notes([60, 62, 64, 65, 67, 69, 71, 72], step=0.75)

    windows = build_bar_window_candidates(notes, _beat_grid(bars=9))

    two_bar = [window for window in windows if window["scale_bars"] == 2]
    four_bar = [window for window in windows if window["scale_bars"] == 4]
    eight_bar = [window for window in windows if window["scale_bars"] == 8]
    assert [window["start_bar"] for window in two_bar[:3]] == [0, 1, 2]
    assert four_bar
    assert eight_bar
    assert all("note_density" in window for window in windows)


def test_similarity_matrix_is_transposition_invariant_for_key_change():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([62, 64, 66, 67], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))

    matrix = build_similarity_matrix(phrases)

    assert len(matrix) == 1
    pair = matrix[0]
    assert pair["estimated_transposition_semitones"] == 2.0
    assert pair["possible_key_change"] is False
    assert pair["transposition_invariant_similarity"] > pair["absolute_contour_similarity"]
    assert pair["transposition_invariant_similarity"] >= 0.9


def test_connected_components_group_transposed_motif_family():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([62, 64, 66, 67], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))
    matrix = build_similarity_matrix(phrases)

    families = build_motif_families(phrases, [], matrix, [])

    assert len(families) == 1
    members = families[0]["members"]
    assert len(members) == 2
    assert any(member["relative_transposition_semitones"] == 2.0 for member in members)
    assert families[0]["family_id"] in {
        family["family_id"] for family in families if family["source"] == "phrase"
    }


def test_octave_transposition_is_recorded_without_key_change_flag():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([72, 74, 76, 77], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))

    pair = build_similarity_matrix(phrases)[0]
    families = build_motif_families(phrases, [], [pair], [])

    assert pair["estimated_transposition_semitones"] == 12.0
    assert pair["possible_key_change"] is False
    assert families[0]["members"][1]["relative_transposition_semitones"] in {0.0, 12.0, -12.0}


def test_interval_match_with_different_rhythm_does_not_create_family():
    fast = _melody_notes([60, 62, 64, 65], start=0.0, step=0.45)
    slow = _melody_notes([62, 64, 66, 67], start=4.0, step=0.9)
    phrases = build_phrase_candidates([*fast, *slow], _beat_grid(bars=6))

    pair = build_similarity_matrix(phrases)[0]
    families = build_motif_families(phrases, [], [pair], [])

    assert pair["interval_contour_similarity"] >= 0.9
    assert pair["rhythm_similarity"] < 0.55
    assert families == []


def test_motif_matches_include_reason_for_high_similarity_pairs():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([62, 64, 66, 67], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))
    matrix = build_similarity_matrix(phrases)

    matches = build_motif_matches(matrix, [])

    assert len(matches) == 1
    assert matches[0]["possible_transposed_match"] is True
    assert matches[0]["transposition_type"] == "ambiguous_transposition"


def test_run_lead_selection_with_missing_inputs_writes_empty_diagnostics(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()

    result = run_lead_selection_diagnostics(artifacts.root)

    assert result.phrase_candidates == []
    diagnostics = json.loads(
        artifacts.melody_lead_selection_diagnostics_json.read_text(encoding="utf-8")
    )
    assert diagnostics["num_phrases"] == 0
    assert "missing_notes_draft" in diagnostics["warnings"]


def test_run_lead_selection_writes_all_phase_3a_artifacts(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.rhythm_dir.mkdir()
    notes = _melody_notes([60, 62, 64, 65], start=0.0)
    artifacts.rhythm_notes_draft_json.write_text(
        json.dumps({"notes": notes}),
        encoding="utf-8",
    )
    artifacts.rhythm_beat_grid_json.write_text(json.dumps(_beat_grid(bars=4)), encoding="utf-8")
    with artifacts.rhythm_vocal_onsets_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["onset_id", "time_sec", "raw_score"])
        writer.writeheader()
        writer.writerow({"onset_id": "onset-0001", "time_sec": 0.0, "raw_score": 0.9})

    run_lead_selection_diagnostics(artifacts.root)

    assert artifacts.melody_lead_selection_phrase_candidates_json.exists()
    assert artifacts.melody_lead_selection_bar_windows_json.exists()
    assert artifacts.melody_lead_selection_phrase_similarity_matrix_json.exists()
    assert artifacts.melody_lead_selection_bar_window_similarity_matrix_json.exists()
    assert artifacts.melody_lead_selection_motif_matches_json.exists()
    assert artifacts.melody_lead_selection_motif_families_json.exists()
    assert artifacts.melody_lead_selection_phrase_motif_family_summary_json.exists()
    assert artifacts.melody_lead_selection_structure_pattern_groups_json.exists()
    assert artifacts.melody_lead_selection_diagnostics_json.exists()


def test_phrase_motif_family_summary_includes_occurrences_duration_and_members():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([60, 62, 64, 65], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))
    matrix = build_similarity_matrix(phrases)
    families = build_motif_families(phrases, [], matrix, [])

    from app.services.melody_lead_selection import build_phrase_motif_family_summary

    summary = build_phrase_motif_family_summary(phrases, families)

    assert summary
    first = summary[0]
    assert first["occurrence_count"] == 2
    assert first["avg_duration_sec"] > 0
    assert first["min_duration_sec"] <= first["max_duration_sec"]
    assert first["members"][0]["phrase_id"].startswith("phrase-")
    assert first["is_fragment_like"] is True
    assert first["summary_eligible"] is False
    assert "reason" in first


def test_long_phrase_motif_family_summary_is_structure_eligible():
    notes_a = _melody_notes([60, 62, 64, 65, 67, 69], start=0.0, step=0.5, duration=0.42)
    notes_b = _melody_notes([60, 62, 64, 65, 67, 69], start=5.0, step=0.5, duration=0.42)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=8))
    matrix = build_similarity_matrix(phrases)
    families = build_motif_families(phrases, [], matrix, [])

    from app.services.melody_lead_selection import build_phrase_motif_family_summary

    summary = build_phrase_motif_family_summary(phrases, families)

    assert summary[0]["avg_duration_sec"] >= 2.0
    assert summary[0]["avg_note_count"] >= 4.0
    assert summary[0]["is_fragment_like"] is False
    assert summary[0]["summary_eligible"] is True


def test_structure_pattern_groups_are_limited_to_ten():
    groups = []
    for index in range(12):
        groups.append(
            {
                "family_id": f"family-{index:04d}",
                "occurrence_count": 2 + index,
                "representative_phrase_id": f"phrase-{index:04d}",
                "mean_similarity": 0.9,
                "avg_duration_sec": 4.0 + index,
                "min_duration_sec": 4.0,
                "max_duration_sec": 5.0,
                "avg_note_count": 6.0,
                "median_register_midi": 60,
                "members": [
                    {
                        "phrase_id": f"phrase-{index:04d}-a",
                        "start_sec": 0.0,
                        "end_sec": 4.0,
                        "duration_sec": 4.0,
                        "start_bar": index,
                        "end_bar": index + 1,
                        "note_count": 6,
                        "relative_transposition_semitones": 0.0,
                        "register_offset_from_family_median": 0.0,
                    },
                    {
                        "phrase_id": f"phrase-{index:04d}-b",
                        "start_sec": 8.0,
                        "end_sec": 12.0,
                        "duration_sec": 4.0,
                        "start_bar": index + 20,
                        "end_bar": index + 21,
                        "note_count": 6,
                        "relative_transposition_semitones": 0.0,
                        "register_offset_from_family_median": 0.0,
                    },
                ],
                "reason": "test",
            }
        )

    payload = build_structure_pattern_groups([], [], [], groups, config={"max_pattern_groups": 10})

    assert len(payload["groups"]) == 10
    assert payload["omitted_groups"]["count"] == 2


def test_fragment_phrase_family_is_kept_out_of_structure_pattern_groups():
    notes_a = _melody_notes([60, 62, 64], start=0.0, step=0.35, duration=0.28)
    notes_b = _melody_notes([60, 62, 64], start=4.0, step=0.35, duration=0.28)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))
    matrix = build_similarity_matrix(phrases)
    families = build_motif_families(phrases, [], matrix, [])

    from app.services.melody_lead_selection import build_phrase_motif_family_summary

    summary = build_phrase_motif_family_summary(phrases, families)
    payload = build_structure_pattern_groups(phrases, [], families, summary)

    assert summary[0]["is_fragment_like"] is True
    assert payload["groups"] == []
    assert payload["low_level_repeated_fragments"]["count"] == 1


def test_two_bar_window_only_family_is_low_level_support():
    windows = [
        {
            "window_id": "barwin-02-0001",
            "source": "bar_window",
            "start_sec": 0.0,
            "end_sec": 4.0,
            "duration_sec": 4.0,
            "start_bar": 0,
            "end_bar": 1,
            "note_count": 8,
            "scale_bars": 2,
        },
        {
            "window_id": "barwin-02-0005",
            "source": "bar_window",
            "start_sec": 8.0,
            "end_sec": 12.0,
            "duration_sec": 4.0,
            "start_bar": 4,
            "end_bar": 5,
            "note_count": 8,
            "scale_bars": 2,
        },
    ]
    families = [
        {
            "family_id": "motif-family-0001",
            "source": "bar_window",
            "members": [{"id": window["window_id"]} for window in windows],
        }
    ]

    payload = build_structure_pattern_groups([], windows, families, [])

    assert payload["groups"] == []
    assert payload["low_level_repeated_fragments"]["count"] == 1


def test_late_consecutive_plus_two_bar_windows_can_be_likely_key_change(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.rhythm_dir.mkdir()
    notes = []
    note_index = 1
    for bar in [0, 1, 8, 9]:
        shift = 2 if bar >= 8 else 0
        for pitch_offset, beat in zip([0, 2, 4, 5], [0.0, 0.45, 0.9, 1.35], strict=False):
            notes.append(_note(note_index, bar * 2.0 + beat, 60 + pitch_offset + shift, duration=0.32))
            note_index += 1
    artifacts.rhythm_notes_draft_json.write_text(json.dumps({"notes": notes}), encoding="utf-8")
    artifacts.rhythm_beat_grid_json.write_text(json.dumps(_beat_grid(bars=12)), encoding="utf-8")

    result = build_lead_selection_diagnostics(artifacts)

    likely = [
        pair
        for pair in result.bar_window_similarity_matrix
        if pair["transposition_type"] == "likely_key_change"
    ]
    assert likely
    assert all(pair["possible_transposed_match"] for pair in likely)


def test_local_plus_three_phrase_is_not_likely_key_change():
    notes_a = _melody_notes([60, 62, 64, 65], start=0.0)
    notes_b = _melody_notes([63, 65, 67, 68], start=4.0)
    phrases = build_phrase_candidates([*notes_a, *notes_b], _beat_grid(bars=6))

    pair = build_similarity_matrix(phrases)[0]

    assert pair["possible_transposed_match"] is True
    assert pair["possible_key_change"] is False
    assert pair["transposition_type"] in {"possible_harmony_like", "ambiguous_transposition"}
    assert pair["transposition_type"] != "likely_key_change"


def test_large_similarity_matrix_output_is_limited():
    phrases = []
    for index in range(12):
        phrase = build_phrase_candidates(
            _melody_notes([60, 62, 64, 65], start=index * 4.0),
            _beat_grid(bars=30),
        )[0]
        phrase["phrase_id"] = f"phrase-{index:04d}"
        phrases.append(phrase)
    matrix = build_similarity_matrix(phrases)

    output = filter_similarity_matrix_for_output(
        matrix,
        config={"similarity_output_min_score": 1.01, "similarity_output_top_k_per_item": 1},
    )

    assert len(matrix) == 66
    assert len(output) < len(matrix)
    assert output


def test_grouping_uses_full_similarity_even_when_output_is_truncated(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")
    artifacts.create_directories()
    artifacts.rhythm_dir.mkdir()
    notes = [
        *_melody_notes([60, 62, 64, 65], start=0.0),
        *_melody_notes([60, 62, 64, 65], start=4.0),
    ]
    artifacts.rhythm_notes_draft_json.write_text(json.dumps({"notes": notes}), encoding="utf-8")
    artifacts.rhythm_beat_grid_json.write_text(json.dumps(_beat_grid(bars=6)), encoding="utf-8")

    result = build_lead_selection_diagnostics(
        artifacts,
        config={"similarity_output_min_score": 1.01, "similarity_output_top_k_per_item": 0},
    )

    assert len(result.phrase_similarity_output) == 0
    assert any(family["source"] == "phrase" for family in result.motif_families)
    assert result.diagnostics["similarity_output_truncated"] is True
