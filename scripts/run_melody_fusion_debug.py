#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.gpu_subprocess_env import build_gpu_subprocess_env  # noqa: E402
from app.services.melody_fusion import FusionConfig, fuse_pitch_csvs  # noqa: E402
from app.services.melody_fusion.io import pitch_csv_rows_from_rmvpe_json, write_pitch_csv  # noqa: E402

BACKENDS = ("rmvpe", "torchcrepe", "fcpe", "pesto")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pitch backends on a WAV, fuse them, and compare each backend with fusion."
    )
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/yt2mp3-melody-fusion-debug"))
    parser.add_argument(
        "--gpu-python",
        type=Path,
        default=Path(os.environ.get("RMVPE_PYTHON", "/home/startech/venvs/yt2mp3-gpu/bin/python")),
    )
    parser.add_argument("--min-successful-backends", type=int, default=2)
    parser.add_argument("--skip", action="append", choices=BACKENDS, default=[])
    parser.add_argument("--keep-rmvpe-json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input_wav.exists():
        raise FileNotFoundError(args.input_wav)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = args.out_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    cache_wav = args.out_dir / "vocals_mono_16000.wav"
    _prepare_cache(args.input_wav, cache_wav)

    statuses: dict[str, dict[str, Any]] = {}
    for backend in BACKENDS:
        if backend in args.skip:
            statuses[backend] = {"status": "skipped"}
            continue
        output_csv = inputs_dir / f"{backend}.csv"
        try:
            if backend == "rmvpe":
                _run_rmvpe(args.gpu_python, cache_wav, output_csv, args.keep_rmvpe_json)
            else:
                _run_csv_backend(args.gpu_python, backend, cache_wav, output_csv)
            statuses[backend] = _summarize_csv(output_csv)
            print(f"{backend}: ok rows={statuses[backend]['rows']}")
        except Exception as exc:
            output_csv.unlink(missing_ok=True)
            statuses[backend] = {"status": "failed", "failed_reason": str(exc)[-500:]}
            print(f"{backend}: failed: {exc}", file=sys.stderr)

    succeeded = [backend for backend, status in statuses.items() if status.get("status") == "succeeded"]
    diagnostics = {
        "input_wav": str(args.input_wav),
        "cache_wav": str(cache_wav),
        "out_dir": str(args.out_dir),
        "required_min_successful_backends": args.min_successful_backends,
        "succeeded_backends": succeeded,
        "backends": statuses,
    }
    if len(succeeded) < args.min_successful_backends:
        diagnostics["fusion_status"] = "failed"
        diagnostics["failed_reason"] = "not_enough_successful_backends"
        _write_json(args.out_dir / "diagnostics.json", diagnostics)
        raise SystemExit(2)

    paths = {backend: inputs_dir / f"{backend}.csv" for backend in succeeded}
    payload = fuse_pitch_csvs(
        rmvpe_csv=paths.get("rmvpe"),
        torchcrepe_csv=paths.get("torchcrepe"),
        fcpe_csv=paths.get("fcpe"),
        pesto_csv=paths.get("pesto"),
        output_json_path=args.out_dir / "fusion.json",
        output_csv_path=args.out_dir / "fusion.csv",
        config=FusionConfig(em_iterations=2),
    )
    comparison = _write_comparison(args.out_dir / "fusion.csv", paths, args.out_dir / "comparison.csv")
    diagnostics["fusion_status"] = "succeeded"
    diagnostics["fusion"] = payload.get("fusion", {})
    diagnostics["comparison"] = comparison
    _write_json(args.out_dir / "diagnostics.json", diagnostics)
    print("fusion:", args.out_dir / "fusion.json")
    print("comparison:", args.out_dir / "comparison.csv")
    print("diagnostics:", args.out_dir / "diagnostics.json")


def _prepare_cache(input_wav: Path, output_wav: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_wav),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ],
        check=True,
    )


def _run_rmvpe(gpu_python: Path, input_wav: Path, output_csv: Path, keep_json: bool) -> None:
    tmp_json = output_csv.with_suffix(".rmvpe.json")
    subprocess.run(
        [
            str(gpu_python),
            str(ROOT / "scripts" / "run_rmvpe_pitch.py"),
            str(input_wav),
            str(tmp_json),
        ],
        check=True,
        env=build_gpu_subprocess_env(mode="onnx", gpu_python=gpu_python),
    )
    rows = pitch_csv_rows_from_rmvpe_json(tmp_json)
    write_pitch_csv(output_csv, rows)
    if not keep_json:
        tmp_json.unlink(missing_ok=True)


def _run_csv_backend(gpu_python: Path, backend: str, input_wav: Path, output_csv: Path) -> None:
    script = {
        "torchcrepe": "run_torchcrepe_pitch_csv.py",
        "fcpe": "run_fcpe_pitch_csv.py",
        "pesto": "run_pesto_pitch_csv.py",
    }[backend]
    subprocess.run(
        [str(gpu_python), str(ROOT / "scripts" / script), str(input_wav), str(output_csv)],
        check=True,
        env=build_gpu_subprocess_env(mode="torch", gpu_python=gpu_python),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _summarize_csv(path: Path) -> dict[str, Any]:
    rows = _read_csv(path)
    voiced = sum(1 for row in rows if row.get("voiced") == "1")
    confidence_kinds = sorted({row.get("confidence_kind") or "none" for row in rows})
    return {
        "status": "succeeded",
        "input_path": str(path),
        "rows": len(rows),
        "voiced_ratio": round(voiced / max(len(rows), 1), 6),
        "confidence_kind": ",".join(confidence_kinds) if confidence_kinds else "none",
    }


def _write_comparison(fusion_csv: Path, backend_paths: dict[str, Path], output_csv: Path) -> dict[str, Any]:
    fusion_rows = {row["time_sec"]: row for row in _read_csv(fusion_csv)}
    backend_rows = {
        backend: {row["time_sec"]: row for row in _read_csv(path)}
        for backend, path in backend_paths.items()
    }
    fieldnames = [
        "time_sec",
        "fusion_f0_hz",
        "fusion_voiced",
        *[f"{backend}_f0_hz" for backend in backend_paths],
        *[f"{backend}_cents_delta" for backend in backend_paths],
    ]
    stats = {backend: {"common_voiced": 0, "agree_50c": 0, "mean_abs_cents": 0.0} for backend in backend_paths}
    sums = {backend: 0.0 for backend in backend_paths}
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for time_sec in sorted(fusion_rows, key=lambda x: float(x)):
            fusion = fusion_rows[time_sec]
            fusion_f0 = _float(fusion.get("f0_hz"))
            row_out: dict[str, Any] = {
                "time_sec": time_sec,
                "fusion_f0_hz": fusion.get("f0_hz", ""),
                "fusion_voiced": fusion.get("voiced", "0"),
            }
            for backend, rows in backend_rows.items():
                backend_row = rows.get(time_sec)
                backend_f0 = _float(backend_row.get("f0_hz")) if backend_row else 0.0
                delta = _cents_delta(backend_f0, fusion_f0)
                row_out[f"{backend}_f0_hz"] = backend_row.get("f0_hz", "") if backend_row else ""
                row_out[f"{backend}_cents_delta"] = "" if delta is None else round(delta, 3)
                if delta is not None:
                    stats[backend]["common_voiced"] += 1
                    sums[backend] += abs(delta)
                    if abs(delta) <= 50:
                        stats[backend]["agree_50c"] += 1
            writer.writerow(row_out)
    for backend, values in stats.items():
        common = values["common_voiced"]
        values["mean_abs_cents"] = round(sums[backend] / common, 3) if common else 0.0
        values["agree_50c_ratio"] = round(values["agree_50c"] / common, 6) if common else 0.0
    return stats


def _float(value: object) -> float:
    try:
        result = float(value)
    except Exception:
        return 0.0
    return result if math.isfinite(result) else 0.0


def _cents_delta(backend_f0: float, fusion_f0: float) -> float | None:
    if backend_f0 <= 0 or fusion_f0 <= 0:
        return None
    return 1200.0 * math.log2(backend_f0 / fusion_f0)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
