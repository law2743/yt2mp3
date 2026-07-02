from app.services.artifacts import JobArtifacts


def test_rhythm_artifact_paths_are_under_analysis_rhythm(tmp_path):
    artifacts = JobArtifacts(tmp_path / "job-id")

    assert artifacts.rhythm_dir == tmp_path / "job-id" / "analysis" / "rhythm"
    assert artifacts.rhythm_beat_grid_json == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "beat_grid.json"
    )
    assert artifacts.rhythm_vocal_onsets_csv == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "vocal_onsets.csv"
    )
    assert artifacts.rhythm_notes_draft_json == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "notes_draft.json"
    )
    assert artifacts.rhythm_notes_draft_csv == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "notes_draft.csv"
    )
    assert artifacts.rhythm_numbered_notation_json == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "numbered_notation.json"
    )
    assert artifacts.rhythm_jianpu_draft_txt == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "jianpu_draft.txt"
    )
    assert artifacts.rhythm_diagnostics_json == (
        tmp_path / "job-id" / "analysis" / "rhythm" / "rhythm_diagnostics.json"
    )
    assert artifacts.melody_postprocessed_csv == (
        tmp_path / "job-id" / "analysis" / "melody" / "fusion" / "postprocessed.csv"
    )
    assert artifacts.melody_postprocessed_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "fusion" / "postprocessed.json"
    )
    assert artifacts.melody_postprocess_diagnostics_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "fusion" / "postprocess_diagnostics.json"
    )
    assert artifacts.melody_lead_selection_dir == (
        tmp_path / "job-id" / "analysis" / "melody" / "lead_selection"
    )
    assert artifacts.melody_lead_selection_phrase_candidates_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "lead_selection" / "phrase_candidates.json"
    )
    assert artifacts.melody_lead_selection_bar_windows_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "lead_selection" / "bar_windows.json"
    )
    assert artifacts.melody_lead_selection_phrase_similarity_matrix_json == (
        tmp_path
        / "job-id"
        / "analysis"
        / "melody"
        / "lead_selection"
        / "phrase_similarity_matrix.json"
    )
    assert artifacts.melody_lead_selection_bar_window_similarity_matrix_json == (
        tmp_path
        / "job-id"
        / "analysis"
        / "melody"
        / "lead_selection"
        / "bar_window_similarity_matrix.json"
    )
    assert artifacts.melody_lead_selection_motif_matches_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "lead_selection" / "motif_matches.json"
    )
    assert artifacts.melody_lead_selection_motif_families_json == (
        tmp_path / "job-id" / "analysis" / "melody" / "lead_selection" / "motif_families.json"
    )
    assert artifacts.melody_lead_selection_phrase_motif_family_summary_json == (
        tmp_path
        / "job-id"
        / "analysis"
        / "melody"
        / "lead_selection"
        / "phrase_motif_family_summary.json"
    )
    assert artifacts.melody_lead_selection_structure_pattern_groups_json == (
        tmp_path
        / "job-id"
        / "analysis"
        / "melody"
        / "lead_selection"
        / "structure_pattern_groups.json"
    )
    assert artifacts.melody_lead_selection_diagnostics_json == (
        tmp_path
        / "job-id"
        / "analysis"
        / "melody"
        / "lead_selection"
        / "lead_selection_diagnostics.json"
    )
