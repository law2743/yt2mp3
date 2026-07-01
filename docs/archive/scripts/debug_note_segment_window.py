from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.artifacts import JobArtifacts  # noqa: E402
from app.services.rhythm.note_draft import (  # noqa: E402
    SegmentationStats,
    Segment,
    _add_warning,
    _add_warnings,
    _annotate_pre_cleanup_segment_flags,
    _cleanup_segments_with_subdivision,
    _load_beat_grid,
    _load_pitch_timeline,
    _load_vocal_onsets,
    _near_segment_start_onset,
    _next_segment,
    _note_name,
    _record_absorbed_warning,
    _result_pitch_source,
    _segment_duration,
    _segment_end,
    _segment_median_midi,
    _segment_pitch_confidence,
    _segment_pitch_stability_cents,
    _segment_pitch_frames_with_boundary_decision,
    _segment_to_note,
    _short_segment_context,
    _should_keep_short_ornament,
    _should_merge_same_pitch_fragment,
    _subdivision_duration_sec,
)
from app.services.rhythm.pipeline import _resolve_pitch_timeline  # noqa: E402


WINDOWS = ((42.30, 43.40), (274.60, 275.70))
B3_MIDI = 59


PARAMS = {
    "min_note_duration_sec": 0.08,
    "max_merge_gap_sec": 0.06,
    "pitch_change_threshold_cents": 80.0,
    "onset_boundary_tolerance_sec": 0.08,
    "strong_pitch_jump_cents": 160.0,
    "weak_pitch_jump_cents": 80.0,
    "same_pitch_cents": 60.0,
    "vocal_onset_tolerance_sec": 0.08,
    "short_spike_max_sec": 0.06,
    "ornament_max_sec": 0.12,
    "vibrato_suppression_cents": 120.0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, default=Path("/tmp/yt2mp3_debug1"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/yt2mp3_debug"))
    parser.add_argument("--copy-dir", type=Path, default=Path("/mnt/d"))
    args = parser.parse_args()

    report = build_report(args.job_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "phase_2_2_3_debug_windows.json"
    txt_path = args.output_dir / "phase_2_2_3_debug_windows.txt"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    txt_path.write_text(render_text_report(report), encoding="utf-8")

    copy_results = copy_outputs(json_path, txt_path, args.copy_dir)
    report["copy_results"] = copy_results
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    txt_path.write_text(render_text_report(report), encoding="utf-8")

    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")
    for line in copy_results:
        print(line)
    return 0


def build_report(job_dir: Path) -> dict[str, Any]:
    artifacts = JobArtifacts(job_dir)
    pitch_path = _resolve_pitch_timeline(artifacts) or artifacts.melody_fusion_csv
    pitch_rows = read_csv_rows(pitch_path)
    pitch = _load_pitch_timeline(pitch_path)
    beat_grid = _load_beat_grid(artifacts.rhythm_beat_grid_json)
    onsets = _load_vocal_onsets(artifacts.rhythm_vocal_onsets_csv)

    stats = SegmentationStats()
    raw_segments = _segment_pitch_frames_with_boundary_decision(
        pitch.frames,
        onsets,
        max_merge_gap_sec=PARAMS["max_merge_gap_sec"],
        pitch_change_threshold_cents=PARAMS["pitch_change_threshold_cents"],
        onset_boundary_tolerance_sec=PARAMS["onset_boundary_tolerance_sec"],
        strong_pitch_jump_cents=PARAMS["strong_pitch_jump_cents"],
        weak_pitch_jump_cents=PARAMS["weak_pitch_jump_cents"],
        same_pitch_cents=PARAMS["same_pitch_cents"],
        vocal_onset_tolerance_sec=PARAMS["vocal_onset_tolerance_sec"],
        vibrato_suppression_cents=PARAMS["vibrato_suppression_cents"],
        stats=stats,
    )
    raw_for_audit = copy.deepcopy(raw_segments)
    cleanup_audit, cleanup_stats = audit_cleanup(
        raw_for_audit,
        beat_grid,
        onsets,
    )
    final_segments = _cleanup_segments_with_subdivision(
        copy.deepcopy(raw_segments),
        beat_grid,
        onsets,
        min_note_duration_sec=PARAMS["min_note_duration_sec"],
        max_merge_gap_sec=PARAMS["max_merge_gap_sec"],
        pitch_change_threshold_cents=PARAMS["pitch_change_threshold_cents"],
        same_pitch_cents=PARAMS["same_pitch_cents"],
        onset_boundary_tolerance_sec=PARAMS["onset_boundary_tolerance_sec"],
        short_spike_max_sec=PARAMS["short_spike_max_sec"],
        ornament_max_sec=PARAMS["ornament_max_sec"],
        stats=cleanup_stats,
    )
    final_notes = [
        _segment_to_note(
            segment,
            index=index,
            pitch_source=_result_pitch_source(pitch.pitch_source),
            onsets=onsets,
            beat_grid=beat_grid,
        )
        for index, segment in enumerate(final_segments)
    ]

    windows = []
    for start, end in WINDOWS:
        window = {
            "start_sec": start,
            "end_sec": end,
            "postprocessed_pitch_frames": pitch_frame_rows(pitch_rows, pitch.frames, start, end),
            "raw_segments_before_cleanup": [
                segment_row(segment, idx)
                for idx, segment in enumerate(raw_segments)
                if overlaps(segment.frames[0].time_sec, _segment_end(segment), start, end)
            ],
            "segments_after_cleanup": [
                row
                for row in cleanup_audit
                if overlaps(row["start_sec"], row["end_sec"], start, end)
            ],
            "final_notes_draft_output": [
                note_row(note, idx)
                for idx, note in enumerate(final_notes)
                if overlaps(note.start_sec, note.end_sec, start, end)
            ],
        }
        window["answers"] = answer_window(window)
        windows.append(window)

    return {
        "schema_version": "phase_2_2_3.debug_windows.v1",
        "job_dir": str(job_dir),
        "pitch_timeline_path": str(pitch_path),
        "pitch_source_used_by_note_draft": pitch.pitch_source,
        "pitch_timeline_warnings": pitch.warnings,
        "params": PARAMS,
        "windows": windows,
        "copy_results": [],
    }


def audit_cleanup(
    segments: list[Segment],
    beat_grid: Any,
    onsets: list[Any],
) -> tuple[list[dict[str, Any]], SegmentationStats]:
    stats = SegmentationStats()
    subdivision_duration_sec = _subdivision_duration_sec(beat_grid)
    min_output_duration_sec = max(
        PARAMS["min_note_duration_sec"],
        min(subdivision_duration_sec, PARAMS["ornament_max_sec"]),
    )
    stats.notes_before_cleanup = len(segments)
    _annotate_pre_cleanup_segment_flags(
        segments,
        onsets,
        min_output_duration_sec=min_output_duration_sec,
        max_merge_gap_sec=PARAMS["max_merge_gap_sec"],
        pitch_change_threshold_cents=PARAMS["pitch_change_threshold_cents"],
        same_pitch_cents=PARAMS["same_pitch_cents"],
        onset_boundary_tolerance_sec=PARAMS["onset_boundary_tolerance_sec"],
    )

    id_by_obj = {id(segment): f"raw-{index + 1:04d}" for index, segment in enumerate(segments)}
    audit = {id_by_obj[id(segment)]: segment_row(segment, index) for index, segment in enumerate(segments)}
    for row in audit.values():
        row.update(
            {
                "kept": False,
                "removed": False,
                "merged": False,
                "absorbed": False,
                "cleanup_action": "pending",
                "cleanup_reason": "",
                "merged_into_segment_id": None,
            }
        )

    cleaned: list[Segment] = []
    pending_absorbed: tuple[str, str] | None = None
    for index, segment in enumerate(segments):
        segment_id = id_by_obj[id(segment)]
        if pending_absorbed:
            warning, absorbed_id = pending_absorbed
            segment.warnings = _add_warning(segment.warnings, warning)
            _record_absorbed_warning(stats, warning)
            audit[absorbed_id]["merged_into_segment_id"] = segment_id
            pending_absorbed = None

        previous = cleaned[-1] if cleaned else None
        previous_id = id_by_obj[id(previous)] if previous is not None else None
        duration = _segment_duration(segment)
        onset = _near_segment_start_onset(
            segment,
            onsets,
            tolerance_sec=PARAMS["onset_boundary_tolerance_sec"],
        )
        next_segment = _next_segment(segments, index)
        next_id = id_by_obj[id(next_segment)] if next_segment is not None else None
        context = _short_segment_context(
            previous,
            segment,
            next_segment,
            max_merge_gap_sec=PARAMS["max_merge_gap_sec"],
            same_pitch_cents=PARAMS["same_pitch_cents"],
        )
        protected_short_note = segment.is_protected_short_note
        is_octave_spike = bool(segment.is_octave_spike or context["is_octave_spike"])

        if previous is not None and _should_merge_same_pitch_fragment(
            previous,
            segment,
            onsets,
            max_merge_gap_sec=PARAMS["max_merge_gap_sec"],
            pitch_change_threshold_cents=PARAMS["pitch_change_threshold_cents"],
            same_pitch_cents=PARAMS["same_pitch_cents"],
            onset_boundary_tolerance_sec=PARAMS["onset_boundary_tolerance_sec"],
        ):
            if protected_short_note and _pitch_distance(previous, segment) >= 150.0:
                stats.overmerge_guard_count += 1
                stats.protected_short_note_count += 1
                segment.warnings = _add_warning(segment.warnings, "protected_short_note")
                cleaned.append(segment)
                mark_kept(audit[segment_id], "protected_short_note")
                continue
            merge_segment_into(previous, segment, warning="merged_same_pitch_fragment")
            stats.merged_same_pitch_count += 1
            mark_merged(audit[segment_id], "merged_same_pitch_fragment", previous_id)
            continue

        if protected_short_note:
            stats.protected_short_note_count += 1
            stats.overmerge_guard_count += 1
            protected_warnings = ["protected_short_note"]
            if onset is not None and duration <= PARAMS["ornament_max_sec"]:
                stats.short_ornament_candidate_count += 1
                stats.kept_short_ornament_count += 1
                protected_warnings.extend(["short_ornament_candidate", "kept_short_ornament"])
            segment.warnings = _add_warnings(segment.warnings, protected_warnings)
            cleaned.append(segment)
            mark_kept(audit[segment_id], ";".join(protected_warnings))
            continue

        if duration + 1e-9 >= min_output_duration_sec:
            cleaned.append(segment)
            mark_kept(audit[segment_id], "duration_ge_min_output_duration")
            continue

        if (
            duration + 1e-9 >= PARAMS["min_note_duration_sec"]
            and onset is None
            and not context["bridges_same_pitch"]
            and not is_octave_spike
        ):
            cleaned.append(segment)
            mark_kept(audit[segment_id], "duration_ge_min_note_no_absorb_context")
            continue

        if (
            duration + 1e-9 >= PARAMS["min_note_duration_sec"]
            and "vocal_onset_same_pitch" in (segment.boundary_reasons or [])
            and onset is not None
            and (onset.confidence or 0.0) >= 0.85
        ):
            cleaned.append(segment)
            mark_kept(audit[segment_id], "strong_same_pitch_onset")
            continue

        if is_octave_spike:
            stats.removed_short_spike_count += 1
            stats.removed_octave_spike_count += 1
            pending_absorbed = audit_absorb(
                audit,
                stats,
                cleaned,
                segment,
                segment_id,
                previous_id,
                next_id,
                "absorbed_octave_spike",
            )
            continue

        if duration <= PARAMS["short_spike_max_sec"] and onset is None and context["bridges_same_pitch"]:
            stats.removed_short_spike_count += 1
            pending_absorbed = audit_absorb(
                audit,
                stats,
                cleaned,
                segment,
                segment_id,
                previous_id,
                next_id,
                "absorbed_short_spike",
            )
            continue

        if onset is not None and _should_keep_short_ornament(
            segment,
            onset,
            context,
            duration_sec=duration,
            min_output_duration_sec=min_output_duration_sec,
            ornament_max_sec=PARAMS["ornament_max_sec"],
        ):
            stats.short_ornament_candidate_count += 1
            stats.kept_short_ornament_count += 1
            segment.warnings = _add_warnings(
                segment.warnings,
                ["short_ornament_candidate", "kept_short_ornament"],
            )
            cleaned.append(segment)
            mark_kept(audit[segment_id], "kept_short_ornament")
            continue

        if onset is not None:
            stats.suppressed_short_ornament_count += 1
            warning = "suppressed_short_ornament"
        else:
            stats.removed_below_min_subdivision_count += 1
            warning = "absorbed_below_min_subdivision"
        pending_absorbed = audit_absorb(
            audit,
            stats,
            cleaned,
            segment,
            segment_id,
            previous_id,
            next_id,
            warning,
        )

    if pending_absorbed and cleaned:
        warning, absorbed_id = pending_absorbed
        cleaned[-1].warnings = _add_warning(cleaned[-1].warnings, warning)
        _record_absorbed_warning(stats, warning)
        audit[absorbed_id]["merged_into_segment_id"] = id_by_obj[id(cleaned[-1])]
    for segment in cleaned:
        segment_id = id_by_obj[id(segment)]
        if audit[segment_id]["cleanup_action"] == "pending":
            mark_kept(audit[segment_id], "kept")
        audit[segment_id]["warnings"] = segment.warnings or []
    stats.notes_after_cleanup = len(cleaned)
    return list(audit.values()), stats


def audit_absorb(
    audit: dict[str, dict[str, Any]],
    stats: SegmentationStats,
    cleaned: list[Segment],
    segment: Segment,
    segment_id: str,
    previous_id: str | None,
    next_id: str | None,
    warning: str,
) -> tuple[str, str] | None:
    if cleaned:
        cleaned[-1].warnings = _add_warning(cleaned[-1].warnings, warning)
        _record_absorbed_warning(stats, warning)
        mark_absorbed(audit[segment_id], warning, previous_id)
        return None
    if next_id is not None:
        mark_absorbed(audit[segment_id], warning, next_id)
        return warning, segment_id
    segment.warnings = _add_warning(segment.warnings, warning)
    _record_absorbed_warning(stats, warning)
    cleaned.append(segment)
    mark_kept(audit[segment_id], f"only_segment_with_warning:{warning}")
    return None


def merge_segment_into(previous: Segment, segment: Segment, *, warning: str) -> None:
    previous.frames.extend(segment.frames)
    previous.warnings = _add_warning(previous.warnings, warning)
    previous.end_boundary_source = segment.end_boundary_source
    previous.end_boundary_reasons = segment.end_boundary_reasons
    previous.end_boundary_confidence = segment.end_boundary_confidence


def mark_kept(row: dict[str, Any], reason: str) -> None:
    row.update({"kept": True, "cleanup_action": "kept", "cleanup_reason": reason})


def mark_merged(row: dict[str, Any], reason: str, into_id: str | None) -> None:
    row.update(
        {
            "removed": True,
            "merged": True,
            "cleanup_action": "merged",
            "cleanup_reason": reason,
            "merged_into_segment_id": into_id,
        }
    )


def mark_absorbed(row: dict[str, Any], reason: str, into_id: str | None) -> None:
    row.update(
        {
            "removed": True,
            "absorbed": True,
            "cleanup_action": "absorbed",
            "cleanup_reason": reason,
            "merged_into_segment_id": into_id,
        }
    )


def _pitch_distance(left: Segment, right: Segment) -> float:
    return abs(_segment_median_midi(left) - _segment_median_midi(right)) * 100.0


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.suffix.lower() != ".csv":
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def pitch_frame_rows(
    csv_rows: list[dict[str, str]],
    parsed_frames: list[Any],
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    parsed_by_time = {round(frame.time_sec, 6): frame for frame in parsed_frames}
    rows: list[dict[str, Any]] = []
    for row in csv_rows:
        time_sec = as_float(row.get("time_sec") or row.get("time") or row.get("t"))
        if time_sec is None or not (start <= time_sec <= end):
            continue
        parsed = parsed_by_time.get(round(time_sec, 6))
        rows.append(
            {
                "time_sec": time_sec,
                "hybrid_postprocessed_midi": optional_float(row.get("hybrid_postprocessed_midi")),
                "hybrid_postprocessed_f0_hz": optional_float(row.get("hybrid_postprocessed_f0_hz")),
                "fusion_postprocessed_midi": optional_float(row.get("fusion_postprocessed_midi")),
                "rmvpe_postprocessed_midi": optional_float(row.get("rmvpe_postprocessed_midi")),
                "actual_note_draft_midi": parsed.midi if parsed else optional_float(row.get("midi")),
                "actual_note_draft_f0_hz": parsed.frequency_hz if parsed else optional_float(row.get("f0_hz")),
                "confidence": optional_float(row.get("confidence")),
                "support_count": optional_float(row.get("support_count")),
                "agreement": optional_float(row.get("agreement")),
                "voiced": row.get("voiced"),
                "missing_columns": missing_postprocessed_columns(row),
            }
        )
    return rows


def segment_row(segment: Segment, index: int) -> dict[str, Any]:
    median_midi = _segment_median_midi(segment)
    midi_note = int(round(median_midi))
    return {
        "segment_id": f"raw-{index + 1:04d}",
        "start_sec": round(segment.frames[0].time_sec, 6),
        "end_sec": round(_segment_end(segment), 6),
        "duration_sec": round(_segment_duration(segment), 6),
        "median_midi": round(median_midi, 6),
        "note_name": _note_name(midi_note),
        "frame_count": len(segment.frames),
        "pitch_stability_cents": round(_segment_pitch_stability_cents(segment), 6),
        "pitch_confidence": round(_segment_pitch_confidence(segment), 6),
        "boundary_reasons": segment.boundary_reasons or [],
        "boundary_confidence": segment.boundary_confidence,
        "boundary_source": segment.boundary_source,
        "warnings": segment.warnings or [],
        "is_protected_short_note": segment.is_protected_short_note,
        "is_octave_spike": segment.is_octave_spike,
        "is_below_min_subdivision": segment.is_below_min_subdivision,
        "is_merge_candidate": segment.is_merge_candidate,
    }


def note_row(note: Any, index: int) -> dict[str, Any]:
    return {
        "note_id": note.note_id or f"note-{index + 1:04d}",
        "start_sec": note.start_sec,
        "end_sec": note.end_sec,
        "duration_sec": note.duration_sec,
        "midi_note": note.midi_note,
        "note_name": note.note_name,
        "boundary_source": note.boundary_source,
        "boundary_reasons": note.boundary_reasons,
        "boundary_confidence": note.boundary_confidence,
        "warnings": note.warnings,
    }


def answer_window(window: dict[str, Any]) -> dict[str, Any]:
    source_b3_frames = [
        row
        for row in window["postprocessed_pitch_frames"]
        if round(row.get("actual_note_draft_midi") or -1) == B3_MIDI
    ]
    source_has_b3 = bool(source_b3_frames)
    raw_b3 = [row for row in window["raw_segments_before_cleanup"] if row["note_name"] == "B3"]
    cleanup_b3 = [row for row in window["segments_after_cleanup"] if row["note_name"] == "B3"]
    cleanup_b3_kept = [row for row in cleanup_b3 if row.get("kept")]
    final_b3 = [row for row in window["final_notes_draft_output"] if row["note_name"] == "B3"]
    containing_raw = containing_segments(source_b3_frames, window["raw_segments_before_cleanup"])
    disappeared_at = "not_disappeared"
    reason = ""
    if not source_has_b3:
        disappeared_at = "pitch_source"
        reason = "pitch source missing B3 frames"
    elif not raw_b3:
        disappeared_at = "_segment_pitch_frames_with_boundary_decision"
        segment_bits = [
            f"{row['segment_id']} median={row['note_name']} warnings={','.join(row.get('warnings') or [])}"
            for row in containing_raw
        ]
        reason = (
            "B3 frames exist in the pitch source, but raw segmentation did not split them "
            "into a B3 median segment; they are inside "
            + ("; ".join(segment_bits) if segment_bits else "non-B3 raw segment(s)")
            + ". This is a median pitch changed / no raw boundary case, not cleanup removal."
        )
    elif not cleanup_b3_kept:
        disappeared_at = "_cleanup_segments_with_subdivision"
        reason = ";".join(sorted({row.get("cleanup_reason") or "" for row in cleanup_b3})) or "cleanup removed B3"
    elif not final_b3:
        disappeared_at = "_segment_to_note"
        reason = "cleanup kept B3 but final note conversion did not emit B3"
    return {
        "pitch_source_has_B3": source_has_b3,
        "raw_segments_have_B3": bool(raw_b3),
        "cleanup_after_has_kept_B3": bool(cleanup_b3_kept),
        "final_notes_have_B3": bool(final_b3),
        "B3_disappeared_at": disappeared_at,
        "disappearance_reason": reason,
        "source_B3_frame_ranges": frame_ranges(source_b3_frames),
        "raw_segments_containing_source_B3": [
            {
                "segment_id": row["segment_id"],
                "start_sec": row["start_sec"],
                "end_sec": row["end_sec"],
                "median_midi": row["median_midi"],
                "note_name": row["note_name"],
                "warnings": row.get("warnings") or [],
            }
            for row in containing_raw
        ],
        "raw_B3_segment_ids": [row["segment_id"] for row in raw_b3],
        "cleanup_B3_actions": [
            {
                "segment_id": row["segment_id"],
                "cleanup_action": row.get("cleanup_action"),
                "cleanup_reason": row.get("cleanup_reason"),
                "merged_into_segment_id": row.get("merged_into_segment_id"),
            }
            for row in cleanup_b3
        ],
    }


def frame_ranges(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not frames:
        return []
    times = sorted(row["time_sec"] for row in frames)
    ranges: list[dict[str, Any]] = []
    start = previous = times[0]
    for time_sec in times[1:]:
        if time_sec - previous > 0.011:
            ranges.append({"start_sec": round(start, 6), "end_sec": round(previous, 6)})
            start = time_sec
        previous = time_sec
    ranges.append({"start_sec": round(start, 6), "end_sec": round(previous, 6)})
    return ranges


def containing_segments(
    frames: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for segment in segments:
        if any(segment["start_sec"] <= frame["time_sec"] < segment["end_sec"] for frame in frames):
            result.append(segment)
    return result


def render_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Conclusion",
        f"job_dir: {report['job_dir']}",
        f"pitch_timeline_path: {report['pitch_timeline_path']}",
        f"pitch_source_used_by_note_draft: {report['pitch_source_used_by_note_draft']}",
        "",
    ]
    for window in report["windows"]:
        answers = window["answers"]
        label = f"{window['start_sec']:.2f}-{window['end_sec']:.2f}"
        lines.extend(
            [
                f"{label}:",
                f"1. pitch source has B3: {yesno(answers['pitch_source_has_B3'])}",
                f"2. raw segments have B3: {yesno(answers['raw_segments_have_B3'])}",
                f"3. cleanup after has kept B3: {yesno(answers['cleanup_after_has_kept_B3'])}",
                f"4. final notes have B3: {yesno(answers['final_notes_have_B3'])}",
                f"5. B3 disappeared at function/step: {answers['B3_disappeared_at']}",
                f"6. disappearance reason: {answers['disappearance_reason'] or 'none'}",
                f"7. cleanup B3 actions: {json.dumps(answers['cleanup_B3_actions'], ensure_ascii=False)}",
                "",
            ]
        )
    for copy_result in report.get("copy_results", []):
        lines.append(f"copy: {copy_result}")
    lines.extend(["", "Details", ""])
    for window in report["windows"]:
        label = f"{window['start_sec']:.2f}-{window['end_sec']:.2f}"
        lines.append(f"## Window {label}")
        for key in (
            "postprocessed_pitch_frames",
            "raw_segments_before_cleanup",
            "segments_after_cleanup",
            "final_notes_draft_output",
        ):
            lines.append(f"### {key}")
            lines.append(json.dumps(window[key], indent=2, ensure_ascii=False))
            lines.append("")
    return "\n".join(lines) + "\n"


def copy_outputs(json_path: Path, txt_path: Path, copy_dir: Path) -> list[str]:
    results: list[str] = []
    if not copy_dir.exists():
        return [f"{copy_dir} does not exist; copy skipped"]
    if not copy_dir.is_dir():
        return [f"{copy_dir} is not a directory; copy skipped"]
    try:
        test_path = copy_dir / ".yt2mp3_write_test"
        test_path.write_text("ok\n", encoding="utf-8")
        test_path.unlink()
    except Exception as exc:
        return [f"{copy_dir} is not writable: {type(exc).__name__}: {exc}; copy skipped"]
    for source in (json_path, txt_path):
        target = copy_dir / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        results.append(f"copied {source} -> {target}")
    return results


def missing_postprocessed_columns(row: dict[str, str]) -> list[str]:
    wanted = (
        "hybrid_postprocessed_midi",
        "hybrid_postprocessed_f0_hz",
        "fusion_postprocessed_midi",
        "rmvpe_postprocessed_midi",
    )
    return [name for name in wanted if name not in row]


def overlaps(left: float, right: float, start: float, end: float) -> bool:
    return left < end and right > start


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    return optional_float(value)


def yesno(value: bool) -> str:
    return "YES" if value else "NO"


if __name__ == "__main__":
    raise SystemExit(main())
