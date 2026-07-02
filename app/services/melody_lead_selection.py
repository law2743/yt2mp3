from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from app.services.artifacts import JobArtifacts

ALGORITHM_VERSION = "lead-selection-phase-3a-v1"

DEFAULT_CONFIG: dict[str, Any] = {
    "min_phrase_notes": 2,
    "min_phrase_duration_sec": 0.35,
    "phrase_gap_split_sec": 0.75,
    "phrase_gap_split_beats": 1.5,
    "phrase_density_split_notes_per_sec": 0.45,
    "bar_window_scales": [2, 4, 8],
    "contour_points": 32,
    "rhythm_bins": 16,
    "match_similarity_threshold": 0.78,
    "family_similarity_threshold": 0.78,
    "family_min_rhythm_similarity": 0.55,
    "possible_key_change_min_abs_semitones": 1.0,
    "possible_key_change_max_abs_semitones": 3.25,
    "possible_key_change_similarity_threshold": 0.78,
    "similarity_output_min_score": 0.78,
    "similarity_output_top_k_per_item": 5,
    "write_full_similarity_matrix": False,
    "max_pattern_groups": 10,
    "fragment_family_max_avg_duration_sec": 2.0,
    "fragment_family_min_avg_note_count": 4.0,
}


@dataclass(frozen=True, slots=True)
class LeadSelectionResult:
    phrase_candidates: list[dict[str, Any]]
    bar_windows: list[dict[str, Any]]
    phrase_similarity_matrix: list[dict[str, Any]]
    bar_window_similarity_matrix: list[dict[str, Any]]
    phrase_similarity_output: list[dict[str, Any]]
    bar_window_similarity_output: list[dict[str, Any]]
    motif_matches: list[dict[str, Any]]
    motif_families: list[dict[str, Any]]
    phrase_motif_family_summary: list[dict[str, Any]]
    structure_pattern_groups: dict[str, Any]
    diagnostics: dict[str, Any]


def run_lead_selection_diagnostics(
    job_dir: Path,
    *,
    config: dict[str, Any] | None = None,
) -> LeadSelectionResult:
    artifacts = JobArtifacts(job_dir)
    merged_config = {**DEFAULT_CONFIG, **(config or {})}
    try:
        result = build_lead_selection_diagnostics(artifacts, config=merged_config)
    except Exception as exc:
        result = _empty_result(
            merged_config,
            errors=[f"lead_selection_failed:{type(exc).__name__}:{str(exc)[:240]}"],
        )
    write_lead_selection_artifacts(artifacts, result)
    return result


def build_lead_selection_diagnostics(
    artifacts: JobArtifacts,
    *,
    config: dict[str, Any] | None = None,
) -> LeadSelectionResult:
    merged_config = {**DEFAULT_CONFIG, **(config or {})}
    warnings: list[str] = []
    errors: list[str] = []

    notes = _load_notes_draft(artifacts, warnings)
    beat_grid = _load_beat_grid(artifacts.rhythm_beat_grid_json, warnings)
    onsets = _load_vocal_onsets(artifacts.rhythm_vocal_onsets_csv, warnings)
    timeline = _load_pitch_timeline(_resolve_pitch_timeline(artifacts), warnings)

    phrases = build_phrase_candidates(notes, beat_grid, onsets, timeline, config=merged_config)
    bar_windows = build_bar_window_candidates(notes, beat_grid, timeline, config=merged_config)
    phrase_similarity = build_similarity_matrix(phrases, config=merged_config)
    bar_similarity = build_similarity_matrix(bar_windows, config=merged_config)
    _classify_transposition_pairs(phrase_similarity, phrases, source="phrase", config=merged_config)
    _classify_transposition_pairs(bar_similarity, bar_windows, source="bar_window", config=merged_config)
    motif_matches = build_motif_matches(phrase_similarity, bar_similarity, config=merged_config)
    motif_families = build_motif_families(
        phrases,
        bar_windows,
        phrase_similarity,
        bar_similarity,
        config=merged_config,
    )
    phrase_summary = build_phrase_motif_family_summary(phrases, motif_families)
    pattern_groups = build_structure_pattern_groups(
        phrases,
        bar_windows,
        motif_families,
        phrase_summary,
        config=merged_config,
    )
    phrase_similarity_output = filter_similarity_matrix_for_output(
        phrase_similarity,
        config=merged_config,
    )
    bar_similarity_output = filter_similarity_matrix_for_output(
        bar_similarity,
        config=merged_config,
    )
    diagnostics = _build_summary(
        phrases,
        bar_windows,
        phrase_similarity,
        bar_similarity,
        phrase_similarity_output,
        bar_similarity_output,
        motif_matches,
        motif_families,
        warnings=warnings,
        errors=errors,
        config=merged_config,
    )
    return LeadSelectionResult(
        phrase_candidates=phrases,
        bar_windows=bar_windows,
        phrase_similarity_matrix=phrase_similarity,
        bar_window_similarity_matrix=bar_similarity,
        phrase_similarity_output=phrase_similarity_output,
        bar_window_similarity_output=bar_similarity_output,
        motif_matches=motif_matches,
        motif_families=motif_families,
        phrase_motif_family_summary=phrase_summary,
        structure_pattern_groups=pattern_groups,
        diagnostics=diagnostics,
    )


def write_lead_selection_artifacts(artifacts: JobArtifacts, result: LeadSelectionResult) -> None:
    _write_json(artifacts.melody_lead_selection_phrase_candidates_json, result.phrase_candidates)
    _write_json(artifacts.melody_lead_selection_bar_windows_json, result.bar_windows)
    _write_json(
        artifacts.melody_lead_selection_phrase_similarity_matrix_json,
        result.phrase_similarity_output,
    )
    _write_json(
        artifacts.melody_lead_selection_bar_window_similarity_matrix_json,
        result.bar_window_similarity_output,
    )
    _write_json(artifacts.melody_lead_selection_motif_matches_json, result.motif_matches)
    _write_json(artifacts.melody_lead_selection_motif_families_json, result.motif_families)
    _write_json(
        artifacts.melody_lead_selection_phrase_motif_family_summary_json,
        result.phrase_motif_family_summary,
    )
    _write_json(
        artifacts.melody_lead_selection_structure_pattern_groups_json,
        result.structure_pattern_groups,
    )
    _write_json(artifacts.melody_lead_selection_diagnostics_json, result.diagnostics)


def build_phrase_candidates(
    notes: list[dict[str, Any]],
    beat_grid: dict[str, Any],
    vocal_onsets: list[float] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if not notes:
        return []
    sorted_notes = sorted(notes, key=lambda note: (note["start_sec"], note["end_sec"]))
    beat_duration = _median_beat_duration(beat_grid)
    max_gap = float(cfg["phrase_gap_split_sec"])
    if beat_duration:
        max_gap = max(max_gap, beat_duration * float(cfg["phrase_gap_split_beats"]))

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for note in sorted_notes:
        if not current:
            current.append(note)
            continue
        previous = current[-1]
        gap = max(0.0, note["start_sec"] - previous["end_sec"])
        duration = max(1e-6, previous["end_sec"] - current[0]["start_sec"])
        density = len(current) / duration
        pitch_jump = abs(float(note["midi_note"]) - float(previous["midi_note"]))
        should_split = (
            gap >= max_gap
            or (gap >= float(cfg["phrase_gap_split_sec"]) * 0.7 and density < float(cfg["phrase_density_split_notes_per_sec"]))
            or (gap >= float(cfg["phrase_gap_split_sec"]) * 0.55 and pitch_jump >= 7)
        )
        if should_split:
            groups.append(current)
            current = [note]
        else:
            current.append(note)
    if current:
        groups.append(current)

    phrases: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        candidate = _candidate_from_notes(
            f"phrase-{index:04d}",
            group,
            beat_grid,
            timeline or [],
            source="phrase",
            vocal_onsets=vocal_onsets or [],
        )
        if (
            candidate["note_count"] >= int(cfg["min_phrase_notes"])
            and candidate["duration_sec"] >= float(cfg["min_phrase_duration_sec"])
        ):
            phrases.append(candidate)
    return phrases


def build_bar_window_candidates(
    notes: list[dict[str, Any]],
    beat_grid: dict[str, Any],
    timeline: list[dict[str, Any]] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bar_starts = _bar_starts(beat_grid)
    if len(bar_starts) < 2:
        return []
    windows: list[dict[str, Any]] = []
    for scale in cfg["bar_window_scales"]:
        scale_bars = int(scale)
        if scale_bars <= 0 or len(bar_starts) <= scale_bars:
            continue
        for start_bar in range(0, len(bar_starts) - scale_bars):
            end_bar = start_bar + scale_bars - 1
            start_sec = bar_starts[start_bar]
            end_sec = bar_starts[start_bar + scale_bars]
            window_notes = [
                note
                for note in notes
                if note["start_sec"] < end_sec and note["end_sec"] > start_sec
            ]
            candidate = _candidate_from_notes(
                f"barwin-{scale_bars:02d}-{start_bar + 1:04d}",
                window_notes,
                beat_grid,
                timeline or [],
                source="bar_window",
                start_sec=start_sec,
                end_sec=end_sec,
                start_bar=start_bar,
                end_bar=end_bar,
            )
            candidate["window_id"] = candidate.pop("phrase_id")
            candidate["scale_bars"] = scale_bars
            windows.append(candidate)
    return windows


def build_similarity_matrix(
    items: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(items):
        for right in items[left_index + 1 :]:
            pairs.append(_similarity_pair(left, right, cfg))
    return pairs


def build_motif_matches(
    phrase_similarity: list[dict[str, Any]],
    bar_window_similarity: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    threshold = float(cfg["match_similarity_threshold"])
    rhythm_min = float(cfg["family_min_rhythm_similarity"])
    matches: list[dict[str, Any]] = []
    for source, matrix in (("phrase", phrase_similarity), ("bar_window", bar_window_similarity)):
        for pair in matrix:
            if (
                pair["transposition_invariant_similarity"] >= threshold
                and pair["rhythm_similarity"] >= rhythm_min
            ):
                reason = "transposition_invariant_contour_and_rhythm"
                if pair.get("transposition_type") == "likely_key_change":
                    reason = "likely_key_change_transposed_motif"
                elif pair.get("possible_transposed_match"):
                    reason = f"{pair.get('transposition_type')}_transposed_motif"
                matches.append(
                    {
                        "source": source,
                        "item_a": pair["item_a"],
                        "item_b": pair["item_b"],
                        "similarity_score": pair["similarity_score"],
                        "transposition_invariant_similarity": pair[
                            "transposition_invariant_similarity"
                        ],
                        "rhythm_similarity": pair["rhythm_similarity"],
                        "estimated_transposition_semitones": pair[
                            "estimated_transposition_semitones"
                        ],
                        "possible_key_change": pair["possible_key_change"],
                        "possible_transposed_match": pair.get("possible_transposed_match", False),
                        "transposition_type": pair.get("transposition_type", "ambiguous_transposition"),
                        "reason": reason,
                    }
                )
    return matches


def build_motif_families(
    phrases: list[dict[str, Any]],
    bar_windows: list[dict[str, Any]],
    phrase_similarity: list[dict[str, Any]],
    bar_window_similarity: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    families: list[dict[str, Any]] = []
    families.extend(
        _families_for_source(
            "phrase",
            "phrase_id",
            phrases,
            phrase_similarity,
            bar_windows,
            start_index=1,
            config=cfg,
        )
    )
    families.extend(
        _families_for_source(
            "bar_window",
            "window_id",
            bar_windows,
            bar_window_similarity,
            [],
            start_index=len(families) + 1,
            config=cfg,
        )
    )
    return families


def build_phrase_motif_family_summary(
    phrases: list[dict[str, Any]],
    motif_families: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    phrase_by_id = {phrase["phrase_id"]: phrase for phrase in phrases}
    summaries: list[dict[str, Any]] = []
    for family in motif_families:
        if family.get("source") != "phrase":
            continue
        members = []
        for member in family.get("members", []):
            phrase_id = member.get("id")
            phrase = phrase_by_id.get(phrase_id)
            if not phrase:
                continue
            members.append(
                {
                    "phrase_id": phrase_id,
                    "start_sec": phrase.get("start_sec"),
                    "end_sec": phrase.get("end_sec"),
                    "duration_sec": phrase.get("duration_sec"),
                    "start_bar": phrase.get("start_bar"),
                    "end_bar": phrase.get("end_bar"),
                    "note_count": phrase.get("note_count"),
                    "relative_transposition_semitones": member.get(
                        "relative_transposition_semitones"
                    ),
                    "register_offset_from_family_median": member.get(
                        "register_offset_from_family_median"
                    ),
                    "possible_transposed_match": member.get("possible_transposed_match", False),
                    "transposition_type": member.get(
                        "transposition_type",
                        "ambiguous_transposition",
                    ),
                }
            )
        if not members:
            continue
        durations = [member["duration_sec"] for member in members if member["duration_sec"] is not None]
        note_counts = [member["note_count"] for member in members if member["note_count"] is not None]
        avg_duration = _round(float(np.mean(durations))) if durations else 0.0
        avg_note_count = _round(float(np.mean(note_counts))) if note_counts else 0.0
        is_fragment_like = bool(avg_duration < 2.0 or avg_note_count < 4.0)
        summaries.append(
            {
                "family_id": family.get("family_id"),
                "occurrence_count": len(members),
                "representative_phrase_id": family.get("representative_phrase_id"),
                "mean_similarity": family.get("mean_similarity"),
                "avg_duration_sec": avg_duration,
                "min_duration_sec": _round(min(durations)) if durations else 0.0,
                "max_duration_sec": _round(max(durations)) if durations else 0.0,
                "avg_note_count": avg_note_count,
                "median_register_midi": family.get("median_register_midi"),
                "is_fragment_like": is_fragment_like,
                "summary_eligible": not is_fragment_like,
                "members": members,
                "reason": _phrase_family_reason(family, members),
            }
        )
    return summaries


def build_structure_pattern_groups(
    phrases: list[dict[str, Any]],
    bar_windows: list[dict[str, Any]],
    motif_families: list[dict[str, Any]],
    phrase_summary: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    phrase_by_id = {phrase["phrase_id"]: phrase for phrase in phrases}
    window_by_id = {window["window_id"]: window for window in bar_windows}
    candidates: list[dict[str, Any]] = []
    low_level: list[dict[str, Any]] = []
    for summary in phrase_summary:
        members = summary.get("members", [])
        candidate = _pattern_group_from_members(
            f"pattern-candidate-phrase-{summary['family_id']}",
            members,
            supporting_phrase_families=[summary["family_id"]],
            supporting_bar_windows=[],
            source="phrase",
        )
        if summary.get("summary_eligible", True):
            candidates.append(candidate)
        else:
            low_level.append(
                {
                    **candidate,
                    "reason": "fragment_like_phrase_family_omitted_from_structure_groups",
                    "is_fragment_like": True,
                }
            )
    for family in motif_families:
        if family.get("source") != "bar_window":
            continue
        members = []
        for member in family.get("members", []):
            window = window_by_id.get(member.get("id"))
            if not window:
                continue
            members.append(
                {
                    "id": window["window_id"],
                    "start_sec": window.get("start_sec"),
                    "end_sec": window.get("end_sec"),
                    "duration_sec": window.get("duration_sec"),
                    "start_bar": window.get("start_bar"),
                    "end_bar": window.get("end_bar"),
                    "note_count": window.get("note_count"),
                    "scale_bars": window.get("scale_bars"),
                    "relative_transposition_semitones": member.get(
                        "relative_transposition_semitones"
                    ),
                    "transposition_type": member.get(
                        "transposition_type",
                        "ambiguous_transposition",
                    ),
                }
            )
        if members:
            supporting_scales = sorted(
                {
                    int(scale)
                    for member in members
                    if (scale := _safe_int(member.get("scale_bars"))) is not None
                }
            )
            candidate = _pattern_group_from_members(
                f"pattern-candidate-bar-{family['family_id']}",
                members,
                supporting_phrase_families=[],
                supporting_bar_windows=[member["id"] for member in members],
                source="bar_window",
            )
            candidate["supporting_bar_window_scales"] = supporting_scales
            has_section_level_support = any(scale >= 4 for scale in supporting_scales)
            if has_section_level_support:
                candidates.append(candidate)
            else:
                low_level.append(
                    {
                        **candidate,
                        "reason": "two_bar_window_only_repeated_fragment",
                        "is_fragment_like": True,
                    }
                )
    candidates = [
        candidate
        for candidate in candidates
        if candidate["occurrence_count"] >= 2
        and (
            any(
                _safe_int(scale) is not None and int(scale) >= 4
                for scale in candidate.get("supporting_bar_window_scales", [])
            )
            or bool(candidate.get("supporting_phrase_families"))
        )
    ]
    low_level = [candidate for candidate in low_level if candidate["occurrence_count"] >= 2]
    ranked = sorted(
        candidates,
        key=lambda group: (
            any(
                _safe_int(scale) is not None and int(scale) >= 4
                for scale in group.get("supporting_bar_window_scales", [])
            ),
            group["occurrence_count"],
            group["total_duration_sec"],
            group["confidence"],
        ),
        reverse=True,
    )
    limit = int(cfg["max_pattern_groups"])
    groups = ranked[:limit]
    for index, group in enumerate(groups, start=1):
        group["pattern_id"] = f"pattern-{index:04d}"
    omitted = ranked[limit:]
    return {
        "schema_version": "lead_selection.structure_pattern_groups.v1",
        "max_pattern_groups": limit,
        "groups": groups,
        "omitted_groups": {
            "count": len(omitted),
            "family_ids": [
                *[
                    family_id
                    for group in omitted
                    for family_id in group.get("supporting_phrase_families", [])
                ],
                *[
                    window_id
                    for group in omitted
                    for window_id in group.get("supporting_bar_windows", [])[:1]
                ],
            ][:50],
            "reason": "limited_to_highest_support_and_duration_groups",
        },
        "low_level_repeated_fragments": {
            "count": len(low_level),
            "groups": low_level[:50],
            "reason": "fragment_like_phrase_families_or_two_bar_only_window_repeats",
        },
    }


def _pattern_group_from_members(
    pattern_id: str,
    members: list[dict[str, Any]],
    *,
    supporting_phrase_families: list[str],
    supporting_bar_windows: list[str],
    source: str,
) -> dict[str, Any]:
    durations = [_safe_float(member.get("duration_sec")) or 0.0 for member in members]
    note_counts = [_safe_float(member.get("note_count")) or 0.0 for member in members]
    start_bars = [_safe_int(member.get("start_bar")) for member in members]
    end_bars = [_safe_int(member.get("end_bar")) for member in members]
    start_bars = [bar for bar in start_bars if bar is not None]
    end_bars = [bar for bar in end_bars if bar is not None]
    transposition_types = {member.get("transposition_type") for member in members}
    avg_duration = float(np.mean(durations)) if durations else 0.0
    occurrence_count = len(members)
    estimated_role = _estimated_pattern_role(
        occurrence_count,
        avg_duration,
        float(np.mean(note_counts)) if note_counts else 0.0,
        source,
    )
    confidence = _clamp01(
        0.25
        + min(0.35, occurrence_count * 0.08)
        + min(0.25, avg_duration / 32.0)
        + (0.15 if supporting_phrase_families and supporting_bar_windows else 0.0)
    )
    return {
        "pattern_id": pattern_id,
        "estimated_role": estimated_role,
        "occurrence_count": occurrence_count,
        "avg_duration_sec": _round(avg_duration),
        "total_duration_sec": _round(sum(durations)),
        "avg_note_count": _round(float(np.mean(note_counts))) if note_counts else 0.0,
        "start_bar_range": [
            min(start_bars) if start_bars else None,
            max(end_bars) if end_bars else None,
        ],
        "members": members,
        "supporting_phrase_families": supporting_phrase_families,
        "supporting_bar_windows": supporting_bar_windows[:24],
        "confidence": _round(confidence),
        "reason": _pattern_reason(estimated_role, occurrence_count, transposition_types),
    }


def _estimated_pattern_role(
    occurrence_count: int,
    avg_duration: float,
    avg_note_count: float,
    source: str,
) -> str:
    if avg_note_count <= 1.5:
        return "sparse_or_instrumental"
    if occurrence_count >= 3 and avg_duration >= 6.0:
        return "chorus_like"
    if occurrence_count >= 2 and avg_duration >= 4.0:
        return "repeated_phrase" if source == "phrase" else "verse_like"
    if occurrence_count == 2 and avg_duration >= 2.0:
        return "repeated_phrase"
    return "ambiguous"


def _phrase_family_reason(family: dict[str, Any], members: list[dict[str, Any]]) -> str:
    transposed = [member for member in members if member.get("possible_transposed_match")]
    if transposed:
        return "similar_phrase_family_with_transposed_members"
    return "similar_phrase_family_by_contour_interval_and_rhythm"


def _pattern_reason(
    estimated_role: str,
    occurrence_count: int,
    transposition_types: set[str | None],
) -> str:
    transposed = sorted(
        value
        for value in transposition_types
        if value and value not in {"none", "ambiguous_transposition"}
    )
    suffix = f"; transposition_signals={','.join(transposed)}" if transposed else ""
    return f"heuristic_{estimated_role}_from_{occurrence_count}_repeated_occurrences{suffix}"


def _families_for_source(
    source: str,
    id_key: str,
    items: list[dict[str, Any]],
    similarity: list[dict[str, Any]],
    supporting_bar_windows: list[dict[str, Any]],
    *,
    start_index: int,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    item_by_id = {item[id_key]: item for item in items}
    edges: dict[str, set[str]] = {item_id: set() for item_id in item_by_id}
    edge_scores: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in similarity:
        if (
            pair["transposition_invariant_similarity"] >= float(config["family_similarity_threshold"])
            and pair["rhythm_similarity"] >= float(config["family_min_rhythm_similarity"])
        ):
            left = pair["item_a"]
            right = pair["item_b"]
            edges.setdefault(left, set()).add(right)
            edges.setdefault(right, set()).add(left)
            edge_scores[_edge_key(left, right)] = pair

    components: list[list[str]] = []
    seen: set[str] = set()
    for item_id in edges:
        if item_id in seen or not edges[item_id]:
            continue
        stack = [item_id]
        component: list[str] = []
        seen.add(item_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in edges[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        if len(component) >= 2:
            components.append(sorted(component))

    families: list[dict[str, Any]] = []
    for offset, component in enumerate(components, start=start_index):
        representative_id = _representative(component, edge_scores)
        representative = item_by_id[representative_id]
        registers = [_safe_float(item_by_id[item_id].get("median_pitch_midi")) for item_id in component]
        registers = [value for value in registers if value is not None]
        family_median = _round(median(registers)) if registers else None
        pair_scores = [
            edge_scores[_edge_key(a, b)]["transposition_invariant_similarity"]
            for index, a in enumerate(component)
            for b in component[index + 1 :]
            if _edge_key(a, b) in edge_scores
        ]
        member_payloads = []
        for member_id in component:
            item = item_by_id[member_id]
            rel = _estimate_transposition(representative, item)
            member_register = _safe_float(item.get("median_pitch_midi"))
            representative_similarity = _similarity_against_representative(
                representative_id,
                member_id,
                edge_scores,
            )
            transposition_type = _transposition_type(
                rel,
                representative_similarity,
                source=source,
                local_only=True,
                config=config,
            )
            member_payloads.append(
                {
                    "id": member_id,
                    "start_sec": item.get("start_sec"),
                    "end_sec": item.get("end_sec"),
                    "duration_sec": item.get("duration_sec"),
                    "start_bar": item.get("start_bar"),
                    "end_bar": item.get("end_bar"),
                    "note_count": item.get("note_count"),
                    "relative_transposition_semitones": _round(rel),
                    "register_offset_from_family_median": _round(
                        member_register - family_median
                    )
                    if member_register is not None and family_median is not None
                    else None,
                    "possible_transposed_match": transposition_type != "none",
                    "transposition_type": transposition_type
                    if transposition_type != "none"
                    else "ambiguous_transposition",
                    "possible_key_change_member": _is_possible_key_change(
                        rel,
                        representative_similarity,
                        config,
                    ),
                }
            )
        all_pitches = [
            pitch for item_id in component for pitch in item_by_id[item_id].get("features", {}).get("pitches", [])
        ]
        family = {
            "family_id": f"motif-family-{offset:04d}",
            "source": source,
            "members": member_payloads,
            "representative_phrase_id": representative_id,
            "mean_similarity": _round(float(np.mean(pair_scores))) if pair_scores else 1.0,
            "median_register_midi": family_median,
            "pitch_range_semitones": _round(max(all_pitches) - min(all_pitches)) if all_pitches else 0.0,
            "variation_level": _variation_level(pair_scores),
            "supporting_bar_windows": _supporting_windows(component, item_by_id, supporting_bar_windows),
        }
        families.append(family)
    return families


def _candidate_from_notes(
    item_id: str,
    notes: list[dict[str, Any]],
    beat_grid: dict[str, Any],
    timeline: list[dict[str, Any]],
    *,
    source: str,
    vocal_onsets: list[float] | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    start_bar: int | None = None,
    end_bar: int | None = None,
) -> dict[str, Any]:
    if notes:
        actual_start = min(note["start_sec"] for note in notes)
        actual_end = max(note["end_sec"] for note in notes)
    else:
        actual_start = start_sec if start_sec is not None else 0.0
        actual_end = end_sec if end_sec is not None else actual_start
    start = float(start_sec if start_sec is not None else actual_start)
    end = float(end_sec if end_sec is not None else actual_end)
    duration = max(0.0, end - start)
    pitches = [float(note["midi_note"]) for note in notes]
    note_durations = [max(0.0, note["end_sec"] - note["start_sec"]) for note in notes]
    voiced_duration = sum(
        max(0.0, min(note["end_sec"], end) - max(note["start_sec"], start)) for note in notes
    )
    gaps = [
        max(0.0, right["start_sec"] - left["end_sec"])
        for left, right in zip(notes, notes[1:], strict=False)
    ]
    gap_duration = sum(gaps)
    voiced_ratio = _voiced_ratio_from_timeline(timeline, start, end)
    if voiced_ratio is None:
        voiced_ratio = voiced_duration / duration if duration > 0 else 0.0
    gap_ratio = min(1.0, gap_duration / duration) if duration > 0 else 0.0
    fragmentation_score = _fragmentation_score(len(notes), duration, gap_ratio)
    bar_start = _time_to_bar(start, beat_grid) if start_bar is None else start_bar
    bar_end = _time_to_bar(max(start, end - 1e-6), beat_grid) if end_bar is None else end_bar
    features = _features_for_notes(notes, start, end)
    onset_count = sum(1 for onset in (vocal_onsets or []) if start <= onset < end)
    payload = {
        "phrase_id": item_id,
        "source": source,
        "start_sec": _round(start),
        "end_sec": _round(end),
        "duration_sec": _round(duration),
        "note_count": len(notes),
        "median_pitch_midi": _round(median(pitches)) if pitches else None,
        "pitch_range_semitones": _round(max(pitches) - min(pitches)) if pitches else 0.0,
        "voiced_ratio": _round(voiced_ratio),
        "gap_ratio": _round(gap_ratio),
        "fragmentation_score": _round(fragmentation_score),
        "start_bar": bar_start,
        "end_bar": bar_end,
        "note_density": _round(len(notes) / duration) if duration > 0 else 0.0,
        "onset_density": _round(onset_count / duration) if duration > 0 else 0.0,
        "features": features,
    }
    payload["features"]["note_durations_sec"] = [_round(value) for value in note_durations]
    return payload


def _features_for_notes(notes: list[dict[str, Any]], start_sec: float, end_sec: float) -> dict[str, Any]:
    pitches = [float(note["midi_note"]) for note in notes]
    if pitches:
        med = float(median(pitches))
        normalized = [pitch - med for pitch in pitches]
        intervals = [right - left for left, right in zip(pitches, pitches[1:], strict=False)]
    else:
        med = 0.0
        normalized = []
        intervals = []
    duration = max(1e-6, end_sec - start_sec)
    onset_positions = [
        min(1.0, max(0.0, (note["start_sec"] - start_sec) / duration)) for note in notes
    ]
    return {
        "pitches": [_round(value) for value in pitches],
        "absolute_pitch_contour": [_round(value) for value in pitches],
        "normalized_pitch_contour": [_round(value) for value in normalized],
        "interval_contour": [_round(value) for value in intervals],
        "rhythm_onset_pattern": [_round(value) for value in onset_positions],
        "median_register": _round(med) if pitches else None,
        "pitch_range": _round(max(pitches) - min(pitches)) if pitches else 0.0,
        "note_density": _round(len(notes) / duration),
    }


def _similarity_pair(left: dict[str, Any], right: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    left_features = left.get("features", {})
    right_features = right.get("features", {})
    absolute = _sequence_similarity(
        left_features.get("absolute_pitch_contour", []),
        right_features.get("absolute_pitch_contour", []),
        points=int(config["contour_points"]),
        center=False,
    )
    normalized = _sequence_similarity(
        left_features.get("normalized_pitch_contour", []),
        right_features.get("normalized_pitch_contour", []),
        points=int(config["contour_points"]),
        center=False,
    )
    interval = _sequence_similarity(
        left_features.get("interval_contour", []),
        right_features.get("interval_contour", []),
        points=max(8, int(config["contour_points"]) - 1),
        center=False,
    )
    rhythm = _rhythm_similarity(
        left_features.get("rhythm_onset_pattern", []),
        right_features.get("rhythm_onset_pattern", []),
        bins=int(config["rhythm_bins"]),
    )
    rhythm *= _density_similarity(left, right)
    transposition = _estimate_transposition(left, right)
    absolute_register_difference = abs(transposition)
    transposition_invariant = _clamp01(0.45 * normalized + 0.30 * interval + 0.25 * rhythm)
    similarity = _clamp01(0.72 * transposition_invariant + 0.28 * absolute)
    left_id = left.get("phrase_id") or left.get("window_id")
    right_id = right.get("phrase_id") or right.get("window_id")
    possible_transposed = _is_possible_transposed_match(
        transposition,
        transposition_invariant,
        config,
    )
    transposition_type = _transposition_type(
        transposition,
        transposition_invariant,
        source=str(left.get("source") or right.get("source") or "unknown"),
        local_only=True,
        config=config,
    )
    return {
        "item_a": left_id,
        "item_b": right_id,
        "similarity_score": _round(similarity),
        "contour_similarity": _round(normalized),
        "absolute_contour_similarity": _round(absolute),
        "normalized_contour_similarity": _round(normalized),
        "interval_contour_similarity": _round(interval),
        "rhythm_similarity": _round(rhythm),
        "estimated_transposition_semitones": _round(transposition),
        "transposition_invariant_similarity": _round(transposition_invariant),
        "absolute_register_difference": _round(absolute_register_difference),
        "possible_key_change": False,
        "possible_transposed_match": possible_transposed,
        "transposition_type": transposition_type
        if possible_transposed
        else "ambiguous_transposition",
        "alignment_method": "resampled_correlation_cosine",
    }


def _density_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_density = _safe_float(left.get("note_density"))
    right_density = _safe_float(right.get("note_density"))
    if not left_density or not right_density:
        return 1.0
    return min(left_density, right_density) / max(left_density, right_density)


def _sequence_similarity(
    left: list[float],
    right: list[float],
    *,
    points: int,
    center: bool,
) -> float:
    if not left or not right:
        return 0.0
    if len(left) == 1 and len(right) == 1:
        distance = abs(float(left[0]) - float(right[0]))
        return _distance_similarity(distance, scale=6.0)
    a = _resample(left, points)
    b = _resample(right, points)
    if center:
        a = a - np.median(a)
        b = b - np.median(b)
    corr = _corr_similarity(a, b)
    distance = float(np.mean(np.abs(a - b)))
    distance_score = _distance_similarity(distance, scale=5.0)
    return _clamp01(0.65 * corr + 0.35 * distance_score)


def _rhythm_similarity(left: list[float], right: list[float], *, bins: int) -> float:
    if not left or not right:
        return 0.0
    a = _rhythm_histogram(left, bins)
    b = _rhythm_histogram(right, bins)
    cosine = _cosine_similarity(a, b)
    count_penalty = 1.0 - min(0.45, abs(len(left) - len(right)) / max(len(left), len(right), 1))
    return _clamp01(cosine * count_penalty)


def _rhythm_histogram(values: list[float], bins: int) -> np.ndarray:
    hist = np.zeros(bins, dtype=float)
    for value in values:
        index = min(bins - 1, max(0, int(float(value) * bins)))
        hist[index] += 1.0
        if index > 0:
            hist[index - 1] += 0.25
        if index + 1 < bins:
            hist[index + 1] += 0.25
    norm = np.linalg.norm(hist)
    return hist / norm if norm else hist


def _resample(values: list[float], points: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return np.zeros(points, dtype=float)
    if arr.size == 1:
        return np.full(points, arr[0], dtype=float)
    x_old = np.linspace(0.0, 1.0, arr.size)
    x_new = np.linspace(0.0, 1.0, points)
    return np.interp(x_new, x_old, arr)


def _corr_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or right.size == 0:
        return 0.0
    if float(np.std(left)) < 1e-6 and float(np.std(right)) < 1e-6:
        return 1.0 if abs(float(np.median(left) - np.median(right))) < 1e-6 else 0.5
    corr = float(np.corrcoef(left, right)[0, 1])
    if math.isnan(corr):
        return 0.0
    return _clamp01((corr + 1.0) / 2.0)


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 0:
        return 0.0
    return _clamp01(float(np.dot(left, right) / denom))


def _distance_similarity(distance: float, *, scale: float) -> float:
    return _clamp01(1.0 - min(1.0, distance / scale))


def _estimate_transposition(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_median = _safe_float(left.get("median_pitch_midi"))
    right_median = _safe_float(right.get("median_pitch_midi"))
    if left_median is None or right_median is None:
        return 0.0
    return right_median - left_median


def _is_possible_key_change(transposition: float, similarity: float | None, config: dict[str, Any]) -> bool:
    abs_shift = abs(transposition)
    return (
        abs_shift >= float(config["possible_key_change_min_abs_semitones"])
        and abs_shift <= float(config["possible_key_change_max_abs_semitones"])
        and (similarity or 0.0) >= float(config["possible_key_change_similarity_threshold"])
    )


def _is_possible_transposed_match(
    transposition: float,
    similarity: float | None,
    config: dict[str, Any],
) -> bool:
    return abs(transposition) >= 0.75 and (similarity or 0.0) >= float(
        config["possible_key_change_similarity_threshold"]
    )


def _transposition_type(
    transposition: float,
    similarity: float | None,
    *,
    source: str,
    local_only: bool,
    config: dict[str, Any],
) -> str:
    if not _is_possible_transposed_match(transposition, similarity, config):
        return "none"
    abs_shift = abs(transposition)
    rounded = int(round(abs_shift))
    if rounded in {3, 4, 5, 7}:
        return "possible_harmony_like"
    if local_only or source == "phrase":
        return "ambiguous_transposition"
    if rounded in {1, 2}:
        return "likely_key_change"
    return "ambiguous_transposition"


def _classify_transposition_pairs(
    matrix: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    source: str,
    config: dict[str, Any],
) -> None:
    item_by_id = {item.get("phrase_id") or item.get("window_id"): item for item in items}
    max_bar = max((_safe_int(item.get("start_bar")) or 0 for item in items), default=0)
    late_threshold = max_bar * 0.5
    late_pairs_by_shift_and_scale: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for pair in matrix:
        transposition = float(pair.get("estimated_transposition_semitones") or 0.0)
        similarity = _safe_float(pair.get("transposition_invariant_similarity"))
        pair["possible_transposed_match"] = _is_possible_transposed_match(
            transposition,
            similarity,
            config,
        )
        pair["transposition_type"] = _transposition_type(
            transposition,
            similarity,
            source=source,
            local_only=True,
            config=config,
        )
        if source != "bar_window" or not pair["possible_transposed_match"]:
            continue
        shift = int(round(transposition))
        if shift not in {1, 2}:
            continue
        right = item_by_id.get(pair["item_b"])
        left = item_by_id.get(pair["item_a"])
        if not left or not right:
            continue
        left_start = _safe_int(left.get("start_bar")) or 0
        right_start = _safe_int(right.get("start_bar")) or 0
        if right_start <= left_start:
            continue
        left_scale = _safe_int(left.get("scale_bars")) or 0
        right_scale = _safe_int(right.get("scale_bars")) or 0
        if left_scale != right_scale or left_scale < 4:
            continue
        later_start = max(
            left_start,
            right_start,
        )
        if later_start >= late_threshold:
            late_pairs_by_shift_and_scale.setdefault((shift, left_scale), []).append(pair)

    likely_pair_ids: set[tuple[str, str]] = set()
    for (_shift, scale), pairs in late_pairs_by_shift_and_scale.items():
        starts_to_pairs: dict[int, list[dict[str, Any]]] = {}
        for pair in pairs:
            left = item_by_id.get(pair["item_a"], {})
            right = item_by_id.get(pair["item_b"], {})
            later_start = max(
                _safe_int(left.get("start_bar")) or 0,
                _safe_int(right.get("start_bar")) or 0,
            )
            starts_to_pairs.setdefault(later_start, []).append(pair)
        late_starts = sorted(starts_to_pairs)
        for left_start, right_start in zip(late_starts, late_starts[1:], strict=False):
            if right_start - left_start <= 1 and scale >= 4:
                likely_pair_ids.update(
                    _edge_key(pair["item_a"], pair["item_b"])
                    for pair in starts_to_pairs[left_start]
                )
                likely_pair_ids.update(
                    _edge_key(pair["item_a"], pair["item_b"])
                    for pair in starts_to_pairs[right_start]
                )

    for pair in matrix:
        if _edge_key(pair["item_a"], pair["item_b"]) in likely_pair_ids:
            pair["transposition_type"] = "likely_key_change"
            pair["possible_key_change"] = True
        elif pair.get("possible_transposed_match") and pair.get("transposition_type") != "likely_key_change":
            pair["possible_key_change"] = False


def filter_similarity_matrix_for_output(
    matrix: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if bool(cfg["write_full_similarity_matrix"]):
        return matrix
    threshold = float(cfg["similarity_output_min_score"])
    top_k = max(0, int(cfg["similarity_output_top_k_per_item"]))
    keep: dict[tuple[str, str], dict[str, Any]] = {}
    per_item: dict[str, list[dict[str, Any]]] = {}
    for pair in matrix:
        if (
            pair.get("similarity_score", 0.0) >= threshold
            or pair.get("transposition_invariant_similarity", 0.0) >= threshold
            or pair.get("possible_transposed_match") is True
        ):
            keep[_edge_key(pair["item_a"], pair["item_b"])] = pair
        per_item.setdefault(pair["item_a"], []).append(pair)
        per_item.setdefault(pair["item_b"], []).append(pair)
    for item_pairs in per_item.values():
        ranked = sorted(
            item_pairs,
            key=lambda pair: (
                pair.get("transposition_invariant_similarity", 0.0),
                pair.get("similarity_score", 0.0),
            ),
            reverse=True,
        )
        for pair in ranked[:top_k]:
            keep[_edge_key(pair["item_a"], pair["item_b"])] = pair
    return sorted(
        keep.values(),
        key=lambda pair: (
            pair.get("item_a", ""),
            pair.get("transposition_invariant_similarity", 0.0),
            pair.get("similarity_score", 0.0),
        ),
        reverse=False,
    )


def _build_summary(
    phrases: list[dict[str, Any]],
    bar_windows: list[dict[str, Any]],
    phrase_similarity: list[dict[str, Any]],
    bar_window_similarity: list[dict[str, Any]],
    phrase_similarity_output: list[dict[str, Any]],
    bar_window_similarity_output: list[dict[str, Any]],
    motif_matches: list[dict[str, Any]],
    motif_families: list[dict[str, Any]],
    *,
    warnings: list[str],
    errors: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    phrase_matches = [match for match in motif_matches if match["source"] == "phrase"]
    bar_matches = [match for match in motif_matches if match["source"] == "bar_window"]
    key_change_matches = [match for match in motif_matches if match.get("possible_key_change")]
    transposed_matches = [match for match in motif_matches if match.get("possible_transposed_match")]
    pairs_computed = len(phrase_similarity) + len(bar_window_similarity)
    pairs_written = len(phrase_similarity_output) + len(bar_window_similarity_output)
    transposed_families = [
        family["family_id"]
        for family in motif_families
        if any(member.get("possible_transposed_match") for member in family.get("members", []))
    ]
    return {
        "schema_version": "lead_selection.diagnostics.v1",
        "algorithm_version": ALGORITHM_VERSION,
        "phase": "3A",
        "num_phrases": len(phrases),
        "num_bar_windows": len(bar_windows),
        "num_phrase_matches": len(phrase_matches),
        "num_bar_window_matches": len(bar_matches),
        "num_motif_families": len(motif_families),
        "num_possible_key_change_matches": len(key_change_matches),
        "num_possible_transposed_matches": len(transposed_matches),
        "num_similarity_pairs_computed": pairs_computed,
        "num_similarity_pairs_written": pairs_written,
        "similarity_output_truncated": pairs_written < pairs_computed,
        "full_similarity_matrix_written": bool(config["write_full_similarity_matrix"]),
        "similarity_output_min_score": float(config["similarity_output_min_score"]),
        "similarity_output_top_k_per_item": int(config["similarity_output_top_k_per_item"]),
        "possible_key_change_regions": [
            {
                "source": match["source"],
                "item_a": match["item_a"],
                "item_b": match["item_b"],
                "estimated_transposition_semitones": match["estimated_transposition_semitones"],
                "similarity_score": match["transposition_invariant_similarity"],
                "possible_transposed_match": match.get("possible_transposed_match", False),
                "transposition_type": match.get("transposition_type", "ambiguous_transposition"),
            }
            for match in key_change_matches
        ],
        "transposed_motif_families": transposed_families,
        "warnings": warnings,
        "errors": errors,
        "config": config,
    }


def _empty_result(config: dict[str, Any], *, errors: list[str] | None = None) -> LeadSelectionResult:
    diagnostics = _build_summary(
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        warnings=[],
        errors=errors or [],
        config=config,
    )
    return LeadSelectionResult(
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        {
            "schema_version": "lead_selection.structure_pattern_groups.v1",
            "max_pattern_groups": int(config.get("max_pattern_groups", 10)),
            "groups": [],
            "omitted_groups": {
                "count": 0,
                "family_ids": [],
                "reason": "no_pattern_groups",
            },
            "low_level_repeated_fragments": {
                "count": 0,
                "groups": [],
                "reason": "no_low_level_repeated_fragments",
            },
        },
        diagnostics,
    )


def _load_notes_draft(artifacts: JobArtifacts, warnings: list[str]) -> list[dict[str, Any]]:
    if artifacts.rhythm_notes_draft_json.exists():
        try:
            payload = json.loads(artifacts.rhythm_notes_draft_json.read_text(encoding="utf-8"))
            notes = payload.get("notes", []) if isinstance(payload, dict) else []
            return [_normalize_note(note) for note in notes if _normalize_note(note)]
        except Exception as exc:
            warnings.append(f"notes_draft_json_read_failed:{type(exc).__name__}")
    if artifacts.rhythm_notes_draft_csv.exists():
        try:
            with artifacts.rhythm_notes_draft_csv.open(newline="", encoding="utf-8") as handle:
                return [
                    normalized
                    for row in csv.DictReader(handle)
                    if (normalized := _normalize_note(row))
                ]
        except Exception as exc:
            warnings.append(f"notes_draft_csv_read_failed:{type(exc).__name__}")
    warnings.append("missing_notes_draft")
    return []


def _normalize_note(note: dict[str, Any]) -> dict[str, Any] | None:
    start = _safe_float(note.get("start_sec"))
    end = _safe_float(note.get("end_sec"))
    midi = _safe_float(note.get("midi_note") or note.get("median_midi") or note.get("midi"))
    if start is None or end is None or midi is None or end <= start:
        return None
    return {
        "note_id": note.get("note_id") or "",
        "start_sec": start,
        "end_sec": end,
        "duration_sec": end - start,
        "midi_note": midi,
        "bar_index": _safe_int(note.get("bar_index")),
    }


def _load_beat_grid(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append("missing_beat_grid")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"beat_grid_read_failed:{type(exc).__name__}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_vocal_onsets(path: Path, warnings: list[str]) -> list[float]:
    if not path.exists():
        warnings.append("missing_vocal_onsets")
        return []
    onsets: list[float] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                value = _safe_float(row.get("time_sec") or row.get("backtracked_time_sec"))
                if value is not None:
                    onsets.append(value)
    except Exception as exc:
        warnings.append(f"vocal_onsets_read_failed:{type(exc).__name__}")
    return sorted(onsets)


def _resolve_pitch_timeline(artifacts: JobArtifacts) -> Path | None:
    for path in (
        artifacts.melody_postprocessed_csv,
        artifacts.melody_postprocessed_json,
        artifacts.melody_fusion_csv,
        artifacts.melody_fusion_json,
    ):
        if path.exists():
            return path
    return None


def _load_pitch_timeline(path: Path | None, warnings: list[str]) -> list[dict[str, Any]]:
    if path is None:
        warnings.append("missing_pitch_timeline")
        return []
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            frames = payload.get("frames") or payload.get("timeline") or []
            return [_normalize_frame(frame) for frame in frames if _normalize_frame(frame)]
        with path.open(newline="", encoding="utf-8") as handle:
            return [_normalize_frame(row) for row in csv.DictReader(handle) if _normalize_frame(row)]
    except Exception as exc:
        warnings.append(f"pitch_timeline_read_failed:{type(exc).__name__}")
        return []


def _normalize_frame(row: dict[str, Any]) -> dict[str, Any] | None:
    time_sec = _safe_float(row.get("time_sec") or row.get("time"))
    midi = _safe_float(
        row.get("hybrid_postprocessed_midi")
        or row.get("hybrid_postprocessed")
        or row.get("midi")
        or row.get("pitch_midi")
    )
    voiced_raw = row.get("voiced")
    voiced = _as_bool(voiced_raw, default=(midi is not None and midi > 0))
    if time_sec is None:
        return None
    return {"time_sec": time_sec, "midi": midi, "voiced": voiced and midi is not None and midi > 0}


def _voiced_ratio_from_timeline(
    timeline: list[dict[str, Any]],
    start_sec: float,
    end_sec: float,
) -> float | None:
    frames = [frame for frame in timeline if start_sec <= frame["time_sec"] < end_sec]
    if not frames:
        return None
    return sum(1 for frame in frames if frame["voiced"]) / len(frames)


def _bar_starts(beat_grid: dict[str, Any]) -> list[float]:
    starts = [_safe_float(value) for value in beat_grid.get("bar_starts_sec", [])]
    starts = [value for value in starts if value is not None]
    if len(starts) >= 2:
        return starts
    beats_per_bar = _safe_int(beat_grid.get("beats_per_bar"))
    beat_times = [_safe_float(value) for value in beat_grid.get("beat_times_sec", [])]
    beat_times = [value for value in beat_times if value is not None]
    if not beats_per_bar or beats_per_bar <= 0 or len(beat_times) < beats_per_bar + 1:
        return []
    starts = [beat_times[index] for index in range(0, len(beat_times), beats_per_bar)]
    if len(starts) >= 2:
        return starts
    return []


def _time_to_bar(time_sec: float, beat_grid: dict[str, Any]) -> int | None:
    starts = _bar_starts(beat_grid)
    if not starts:
        return None
    bar = 0
    for index, start in enumerate(starts):
        if start <= time_sec:
            bar = index
        else:
            break
    return bar


def _median_beat_duration(beat_grid: dict[str, Any]) -> float | None:
    beat_times = [_safe_float(value) for value in beat_grid.get("beat_times_sec", [])]
    beat_times = [value for value in beat_times if value is not None]
    intervals = [
        right - left
        for left, right in zip(beat_times, beat_times[1:], strict=False)
        if right > left
    ]
    return float(median(intervals)) if intervals else None


def _fragmentation_score(note_count: int, duration: float, gap_ratio: float) -> float:
    if duration <= 0:
        return 0.0
    density = note_count / duration
    return _clamp01(0.55 * min(1.0, density / 8.0) + 0.45 * gap_ratio)


def _representative(component: list[str], edge_scores: dict[tuple[str, str], dict[str, Any]]) -> str:
    best_id = component[0]
    best_score = -1.0
    for item_id in component:
        scores = [
            payload["transposition_invariant_similarity"]
            for key, payload in edge_scores.items()
            if item_id in key
        ]
        score = float(np.mean(scores)) if scores else 0.0
        if score > best_score:
            best_id = item_id
            best_score = score
    return best_id


def _similarity_against_representative(
    representative_id: str,
    member_id: str,
    edge_scores: dict[tuple[str, str], dict[str, Any]],
) -> float | None:
    if representative_id == member_id:
        return 1.0
    pair = edge_scores.get(_edge_key(representative_id, member_id))
    if not pair:
        return None
    return pair["transposition_invariant_similarity"]


def _supporting_windows(
    component: list[str],
    item_by_id: dict[str, dict[str, Any]],
    bar_windows: list[dict[str, Any]],
) -> list[str]:
    if not bar_windows:
        return []
    support: list[str] = []
    for window in bar_windows:
        for member_id in component:
            item = item_by_id[member_id]
            if _bars_overlap(item, window):
                support.append(window["window_id"])
                break
    return support[:12]


def _bars_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = _safe_int(left.get("start_bar"))
    left_end = _safe_int(left.get("end_bar"))
    right_start = _safe_int(right.get("start_bar"))
    right_end = _safe_int(right.get("end_bar"))
    if None in {left_start, left_end, right_start, right_end}:
        return False
    return left_start <= right_end and right_start <= left_end


def _variation_level(scores: list[float]) -> str:
    if not scores:
        return "low"
    mean_score = float(np.mean(scores))
    if mean_score >= 0.9:
        return "low"
    if mean_score >= 0.82:
        return "medium"
    return "high"


def _edge_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else None


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _round(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
