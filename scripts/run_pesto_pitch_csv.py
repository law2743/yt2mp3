#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path


FIELDS = ("time_sec", "f0_hz", "raw_f0_hz", "confidence", "confidence_kind", "voiced", "backend")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PESTO pitch to standardized CSV.")
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--confidence-threshold", type=float, default=0.20)
    parser.add_argument("--step-ms", type=int, default=10)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--work-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input_wav.exists():
        raise FileNotFoundError(args.input_wav)
    exe = shutil.which("pesto") or str(Path(sys.executable).with_name("pesto"))
    if not Path(exe).exists() and not shutil.which(exe):
        raise RuntimeError("Cannot find `pesto` CLI in PATH.")
    work_dir = args.work_dir or args.output_csv.parent / ".pesto-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            exe,
            str(args.input_wav),
            "--gpu",
            str(args.gpu),
            "-s",
            str(args.step_ms),
            "-o",
            str(work_dir),
            "-e",
            "csv",
        ],
        check=True,
    )
    source_csv = work_dir / f"{args.input_wav.stem}.f0.csv"
    if not source_csv.exists():
        matches = sorted(work_dir.glob("*.f0.csv"))
        if not matches:
            raise FileNotFoundError(f"PESTO CSV not found under {work_dir}")
        source_csv = matches[-1]

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with source_csv.open(newline="", encoding="utf-8") as src, args.output_csv.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=FIELDS)
        writer.writeheader()
        for row in reader:
            t_ms = float(row["time"])
            hz = float(row["frequency"])
            conf = float(row["confidence"])
            voiced = hz > 0 and conf >= args.confidence_threshold
            writer.writerow(
                {
                    "time_sec": round(t_ms / 1000.0, 6),
                    "f0_hz": round(hz if voiced else 0.0, 6),
                    "raw_f0_hz": round(hz, 6),
                    "confidence": round(conf, 6),
                    "confidence_kind": "voicing",
                    "voiced": 1 if voiced else 0,
                    "backend": "pesto",
                }
            )


if __name__ == "__main__":
    main()
