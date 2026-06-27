#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import librosa
import torch
import torchcrepe


FIELDS = ("time_sec", "f0_hz", "raw_f0_hz", "confidence", "confidence_kind", "voiced", "backend")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract torchcrepe pitch to standardized CSV.")
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--periodicity-threshold", type=float, default=0.60)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--hop-size", type=int, default=160)
    parser.add_argument("--fmin", type=float, default=50)
    parser.add_argument("--fmax", type=float, default=1100)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--model", choices=["tiny", "full"], default="full")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input_wav.exists():
        raise FileNotFoundError(args.input_wav)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    audio, _ = librosa.load(str(args.input_wav), sr=args.sample_rate, mono=True)
    audio_t = torch.from_numpy(audio).float().unsqueeze(0).to(device)
    with torch.inference_mode():
        pitch, periodicity = torchcrepe.predict(
            audio_t,
            args.sample_rate,
            args.hop_size,
            fmin=args.fmin,
            fmax=args.fmax,
            model=args.model,
            batch_size=args.batch_size,
            device=device,
            return_periodicity=True,
        )
    pitch_values = pitch.detach().cpu().reshape(-1).tolist()
    periodicity_values = periodicity.detach().cpu().reshape(-1).tolist()
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for index, (hz, conf) in enumerate(zip(pitch_values, periodicity_values, strict=True)):
            hz = float(hz)
            conf = float(conf)
            voiced = hz > 0 and conf >= args.periodicity_threshold
            writer.writerow(
                {
                    "time_sec": round(index * args.hop_size / args.sample_rate, 6),
                    "f0_hz": round(hz if voiced else 0.0, 6),
                    "raw_f0_hz": round(hz, 6),
                    "confidence": round(conf, 6),
                    "confidence_kind": "periodicity",
                    "voiced": 1 if voiced else 0,
                    "backend": "torchcrepe",
                }
            )


if __name__ == "__main__":
    main()
