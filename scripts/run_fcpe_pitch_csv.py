#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from typing import Any

from pathlib import Path

import librosa
import torch
import torchfcpe


FIELDS = ("time_sec", "f0_hz", "raw_f0_hz", "confidence", "confidence_kind", "voiced", "backend")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract FCPE pitch to standardized CSV.")
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--threshold", type=float, default=0.03)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--hop-size", type=int, default=160)
    return parser.parse_args()


def _to_list(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "reshape"):
        value = value.reshape(-1)
    if hasattr(value, "tolist"):
        return [float(x) for x in value.tolist()]
    return [float(x) for x in value]


def _extract_pitch_and_peak_probability(result: Any) -> tuple[list[float], list[float] | None]:
    pitch = result
    probabilities = None
    if isinstance(result, dict):
        for key in ("f0", "pitch", "frequency"):
            if key in result:
                pitch = result[key]
                break
        for key in ("probabilities", "probs", "softmax", "distribution"):
            if key in result:
                probabilities = result[key]
                break
    elif isinstance(result, (tuple, list)) and result:
        pitch = result[0]
        for candidate in result[1:]:
            if hasattr(candidate, "shape") and len(getattr(candidate, "shape", ())) >= 2:
                probabilities = candidate
                break
    peak_probability = None
    if probabilities is not None and hasattr(probabilities, "detach"):
        probs = probabilities.detach().cpu()
        if probs.ndim >= 2:
            peak_probability = probs.reshape(-1, probs.shape[-1]).max(dim=-1).values.tolist()
    return _to_list(pitch), [float(x) for x in peak_probability] if peak_probability else None


def main() -> None:
    args = parse_args()
    if not args.input_wav.exists():
        raise FileNotFoundError(args.input_wav)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    audio, _ = librosa.load(str(args.input_wav), sr=args.sample_rate, mono=True)
    wav = torch.from_numpy(audio).float()
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    model = torchfcpe.spawn_bundled_infer_model(device=device)
    with torch.inference_mode():
        result = model.infer(
            wav,
            sr=args.sample_rate,
            decoder_mode="local_argmax",
            threshold=args.threshold,
        )
    pitch_values, peak_probabilities = _extract_pitch_and_peak_probability(result)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for index, hz in enumerate(pitch_values):
            hz = float(hz)
            voiced = hz > 0
            peak_probability = (
                peak_probabilities[index]
                if peak_probabilities is not None and index < len(peak_probabilities)
                else None
            )
            writer.writerow(
                {
                    "time_sec": round(index * args.hop_size / args.sample_rate, 6),
                    "f0_hz": round(hz if voiced else 0.0, 6),
                    "raw_f0_hz": round(hz, 6),
                    "confidence": "" if peak_probability is None else round(peak_probability, 6),
                    "confidence_kind": "peak_probability" if peak_probability is not None else "none",
                    "voiced": 1 if voiced else 0,
                    "backend": "fcpe",
                }
            )


if __name__ == "__main__":
    main()
