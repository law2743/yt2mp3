#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from run_pesto_pitch_csv import main as run_pesto_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test PESTO and export standardized pitch CSV.")
    parser.add_argument("input_wav", type=Path, help="Input vocals WAV")
    parser.add_argument("output_csv", type=Path, nargs="?", default=Path("/tmp/pesto.csv"))
    parser.add_argument("--confidence-threshold", type=float, default=0.20)
    parser.add_argument("--step-ms", type=int, default=10)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--work-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import sys

    argv = [
        "run_pesto_pitch_csv.py",
        str(args.input_wav),
        str(args.output_csv),
        "--confidence-threshold",
        str(args.confidence_threshold),
        "--step-ms",
        str(args.step_ms),
        "--gpu",
        str(args.gpu),
    ]
    if args.work_dir:
        argv.extend(["--work-dir", str(args.work_dir)])
    sys.argv = argv
    run_pesto_main()
    _print_summary(args.output_csv, "pesto")


def _print_summary(path: Path, backend: str) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    voiced = sum(1 for row in rows if row.get("voiced") == "1")
    confidence_kind = next((row.get("confidence_kind") for row in rows if row.get("confidence_kind")), "none")
    print("backend:", backend)
    print("frames:", len(rows))
    print("voiced:", voiced)
    print("voiced ratio:", round(voiced / max(len(rows), 1), 3))
    print("confidence_kind:", confidence_kind)
    print("output:", path)


if __name__ == "__main__":
    main()
