#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two notes_draft.csv files.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    baseline = _read_notes(args.baseline)
    candidate = _read_notes(args.candidate)
    report = compare_notes(baseline, candidate, args.baseline, args.candidate)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report)


def compare_notes(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    baseline_path: Path,
    candidate_path: Path,
) -> str:
    lines = [
        "notes_draft compare report",
        f"baseline: {baseline_path}",
        f"candidate: {candidate_path}",
        "",
        "[baseline]",
        *_summary_lines(baseline),
        "",
        "[candidate]",
        *_summary_lines(candidate),
        "",
        "[delta]",
        f"note_count: {len(candidate) - len(baseline):+d}",
        f"total_duration: {_total_duration(candidate) - _total_duration(baseline):+.6f}",
        "",
        "[examples of large differences]",
        *_large_difference_lines(baseline, candidate),
    ]
    return "\n".join(lines) + "\n"


def _read_notes(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append(
                {
                    **row,
                    "duration_sec": _float(row.get("duration_sec")),
                    "start_sec": _float(row.get("start_sec")),
                    "end_sec": _float(row.get("end_sec")),
                    "midi_note": _int(row.get("midi_note")),
                    "warnings": _split_tokens(row.get("warnings")),
                    "boundary_reasons": _split_tokens(row.get("boundary_reasons")),
                }
            )
        return rows


def _summary_lines(rows: list[dict[str, Any]]) -> list[str]:
    durations = [row["duration_sec"] for row in rows if row["duration_sec"] is not None]
    return [
        f"note_count: {len(rows)}",
        f"total_duration: {_total_duration(rows):.6f}",
        f"mean_duration: {mean(durations):.6f}" if durations else "mean_duration: 0.000000",
        f"median_duration: {median(durations):.6f}" if durations else "median_duration: 0.000000",
        f"duration < 0.06s count: {_duration_count(durations, upper=0.06)}",
        f"duration < 0.08s count: {_duration_count(durations, upper=0.08)}",
        f"duration < 0.10s count: {_duration_count(durations, upper=0.10)}",
        f"duration 0.10~0.25s count: {_duration_count(durations, lower=0.10, upper=0.25)}",
        "warning summary: " + _format_counter(_token_counter(rows, "warnings")),
        "midi_note distribution: " + _format_counter(Counter(row["midi_note"] for row in rows if row["midi_note"] is not None)),
        "boundary_source summary: " + _format_counter(Counter(row.get("boundary_source") or "" for row in rows)),
        "boundary_reasons summary: " + _format_counter(_token_counter(rows, "boundary_reasons")),
    ]


def _large_difference_lines(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[str]:
    pairs = zip(baseline, candidate, strict=False)
    differences: list[tuple[float, str]] = []
    for index, (left, right) in enumerate(pairs, start=1):
        duration_delta = abs((right.get("duration_sec") or 0.0) - (left.get("duration_sec") or 0.0))
        midi_delta = abs((right.get("midi_note") or 0) - (left.get("midi_note") or 0))
        start_delta = abs((right.get("start_sec") or 0.0) - (left.get("start_sec") or 0.0))
        score = duration_delta + start_delta + midi_delta * 0.05
        if score <= 0:
            continue
        differences.append(
            (
                score,
                "note {index}: base midi={bm} start={bs:.3f} dur={bd:.3f} | "
                "cand midi={cm} start={cs:.3f} dur={cd:.3f}".format(
                    index=index,
                    bm=left.get("midi_note"),
                    bs=left.get("start_sec") or 0.0,
                    bd=left.get("duration_sec") or 0.0,
                    cm=right.get("midi_note"),
                    cs=right.get("start_sec") or 0.0,
                    cd=right.get("duration_sec") or 0.0,
                ),
            )
        )
    differences.sort(reverse=True, key=lambda item: item[0])
    lines = [line for _score, line in differences[:limit]]
    if len(baseline) != len(candidate):
        lines.append(f"unpaired_notes: baseline={len(baseline)} candidate={len(candidate)}")
    return lines or ["no paired note differences"]


def _duration_count(
    durations: list[float],
    *,
    lower: float | None = None,
    upper: float,
) -> int:
    return sum(
        1
        for duration in durations
        if (lower is None or duration >= lower) and duration < upper
    )


def _total_duration(rows: list[dict[str, Any]]) -> float:
    return sum(row["duration_sec"] for row in rows if row["duration_sec"] is not None)


def _token_counter(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(row.get(key) or [])
    return counter


def _format_counter(counter: Counter[Any], *, limit: int = 16) -> str:
    if not counter:
        return "(none)"
    return ", ".join(f"{key}:{count}" for key, count in counter.most_common(limit))


def _split_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [token.strip() for token in value.replace(",", ";").split(";") if token.strip()]


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in {None, ""} else None
    except ValueError:
        return None


if __name__ == "__main__":
    main()
