#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from run_fcpe_pitch_csv import main as run_fcpe_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test FCPE and export standardized pitch CSV.")
    parser.add_argument("input_wav", type=Path, help="Input vocals WAV")
    parser.add_argument("output_csv", type=Path, nargs="?", default=Path("/tmp/fcpe.csv"))
    parser.add_argument("--threshold", type=float, default=0.03)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--hop-size", type=int, default=160)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import sys

    sys.argv = [
        "run_fcpe_pitch_csv.py",
        str(args.input_wav),
        str(args.output_csv),
        "--threshold",
        str(args.threshold),
        "--sample-rate",
        str(args.sample_rate),
        "--hop-size",
        str(args.hop_size),
    ]
    run_fcpe_main()
    _print_summary(args.output_csv, "fcpe")


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
