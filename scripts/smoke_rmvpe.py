#!/usr/bin/env python3
"""Smoke-test RMVPE pitch extraction on a Demucs vocals.wav file.

This script is intentionally standalone:
- It does NOT import the FastAPI app.
- It does NOT change frontend code.
- It does NOT depend on pYIN/librosa fallback.
- It requires a GPU-capable ONNX Runtime CUDA provider.

Usage:
    python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav

Output:
    /path/to/analysis/pitch/vocal_pitch.json
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.gpu_subprocess_env import build_gpu_subprocess_env  # noqa: E402


DEFAULT_CONFIDENCE_THRESHOLD = 0.03
EXPECTED_INPUT_NAME = "vocals.wav"
GPU_ENV_READY = "YT2MP3_GPU_ENV_READY"
RMVPE_PYTHON = Path(
    os.environ.get("RMVPE_PYTHON", "/home/startech/venvs/yt2mp3-gpu/bin/python")
)


class SmokeError(RuntimeError):
    """Expected smoke-test failure with a clear user-facing message."""


@dataclass(frozen=True)
class PredictionResult:
    time: np.ndarray
    frequency: np.ndarray
    confidence: np.ndarray
    activation_shape: list[int] | None


def _die(message: str, *, exit_code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def _as_float_list(value: Any) -> list[float]:
    arr = np.asarray(value).reshape(-1)
    return [float(x) for x in arr]


def _describe_import_error(module_name: str, err: BaseException) -> str:
    return (
        f"Cannot import {module_name!r}: {err}\n"
        "Install it only in the GPU venv, for example:\n"
        "  python -m pip install rmvpe-onnx soundfile\n"
        "If you want GPU execution, install a compatible onnxruntime-gpu and make sure "
        "CUDAExecutionProvider is available. Do not fallback to pYIN for this smoke test."
    )


def _require_cuda_execution_provider() -> tuple[Any, list[str]]:
    try:
        ort = import_module("onnxruntime")
    except Exception as err:  # pragma: no cover - environment specific
        raise SmokeError(_describe_import_error("onnxruntime", err)) from err

    providers = list(getattr(ort, "get_available_providers")())
    if "CUDAExecutionProvider" not in providers:
        raise SmokeError(
            "onnxruntime is installed, but CUDAExecutionProvider is not available.\n"
            f"Available providers: {providers}\n"
            "This smoke test intentionally refuses CPU fallback. In the GPU venv, keep only "
            "a CUDA-capable ONNX Runtime package, for example:\n"
            "  python -m pip uninstall -y onnxruntime\n"
            "  python -m pip install onnxruntime-gpu\n"
        )
    return ort, providers


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        sf = import_module("soundfile")
    except Exception as err:  # pragma: no cover - environment specific
        raise SmokeError(_describe_import_error("soundfile", err)) from err

    try:
        audio, sr = sf.read(str(path), always_2d=False, dtype="float32")
    except Exception as err:
        raise SmokeError(f"Failed to read audio file {path}: {err}") from err

    audio_np = np.asarray(audio, dtype=np.float32)
    if audio_np.ndim == 2:
        # Demucs stems may be stereo; RMVPE expects a mono waveform in common wrappers.
        audio_np = audio_np.mean(axis=1, dtype=np.float32)
    elif audio_np.ndim != 1:
        raise SmokeError(f"Unsupported audio shape {audio_np.shape}; expected mono or stereo WAV.")

    if audio_np.size == 0:
        raise SmokeError("Audio is empty.")
    if not np.isfinite(audio_np).all():
        raise SmokeError("Audio contains NaN or Inf values.")

    return audio_np, int(sr)


def _import_rmvpe_class() -> tuple[type[Any], str, str | None]:
    try:
        module = import_module("rmvpe_onnx")
    except Exception as err:  # pragma: no cover - environment specific
        raise SmokeError(_describe_import_error("rmvpe_onnx", err)) from err

    cls = getattr(module, "RMVPE", None)
    if cls is None or not inspect.isclass(cls):
        public = [name for name in dir(module) if not name.startswith("_")]
        raise SmokeError(
            "Imported rmvpe_onnx, but could not find class RMVPE.\n"
            f"Public names found: {public}\n"
            "Package API may have changed; inspect the installed package before integrating."
        )

    version = getattr(module, "__version__", None)
    return cls, getattr(module, "__file__", "<unknown>"), version


def _candidate_constructor_kwargs(sig: inspect.Signature) -> list[dict[str, Any]]:
    """Build conservative constructor attempts based on inspected parameter names.

    We do not blindly assume a specific API. The default rmvpe-onnx README shows
    RMVPE() with no args, but some wrappers expose provider/device knobs.
    """
    params = sig.parameters
    attempts: list[dict[str, Any]] = []

    # Prefer explicit CUDA provider if the installed class advertises such a parameter.
    for key in ("providers", "execution_providers"):
        if key in params:
            attempts.append({key: ["CUDAExecutionProvider"]})
    for key in ("provider", "execution_provider"):
        if key in params:
            attempts.append({key: "CUDAExecutionProvider"})
    for key in ("device",):
        if key in params:
            attempts.append({key: "cuda"})

    # README-compatible default constructor.
    attempts.append({})

    # De-duplicate while preserving order.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for kwargs in attempts:
        marker = json.dumps(kwargs, sort_keys=True)
        if marker not in seen:
            unique.append(kwargs)
            seen.add(marker)
    return unique


def _instantiate_rmvpe(cls: type[Any]) -> tuple[Any, dict[str, Any], str]:
    try:
        sig = inspect.signature(cls)
    except Exception:
        sig = inspect.Signature()

    errors: list[str] = []
    for kwargs in _candidate_constructor_kwargs(sig):
        try:
            instance = cls(**kwargs)
            return instance, kwargs, str(sig)
        except Exception as err:
            errors.append(f"RMVPE({kwargs}) failed: {type(err).__name__}: {err}")

    raise SmokeError(
        "Could not instantiate rmvpe_onnx.RMVPE using inspected constructor.\n"
        f"Signature: {sig}\n" + "\n".join(errors)
    )


def _iter_possible_sessions(obj: Any) -> Iterable[Any]:
    """Yield nested ONNX Runtime sessions if the wrapper exposes them.

    This is only for diagnostics. Different wrappers use different attribute names.
    """
    seen: set[int] = set()
    stack = [obj]
    while stack:
        cur = stack.pop()
        obj_id = id(cur)
        if obj_id in seen:
            continue
        seen.add(obj_id)

        if hasattr(cur, "get_providers") and callable(cur.get_providers):
            yield cur
            continue

        for name in ("session", "sess", "ort_session", "model", "net", "predictor"):
            child = getattr(cur, name, None)
            if child is not None:
                stack.append(child)


def _session_provider_report(rmvpe: Any) -> list[list[str]]:
    reports: list[list[str]] = []
    for session in _iter_possible_sessions(rmvpe):
        try:
            reports.append(list(session.get_providers()))
        except Exception:
            continue
    return reports


def _validate_session_uses_cuda(rmvpe: Any) -> list[list[str]]:
    reports = _session_provider_report(rmvpe)
    if not reports:
        # Some wrappers hide the session. We still required CUDAExecutionProvider to be installed.
        # Keep this as a warning instead of failing because the provider may be selected lazily.
        print(
            "WARNING: Could not inspect RMVPE internal ONNX session providers; "
            "CUDAExecutionProvider is available globally, continuing.",
            file=sys.stderr,
        )
        return reports

    if not any("CUDAExecutionProvider" in providers for providers in reports):
        raise SmokeError(
            "RMVPE internal ONNX session does not list CUDAExecutionProvider.\n"
            f"Session provider reports: {reports}\n"
            "This smoke test intentionally refuses CPU fallback."
        )
    return reports


def _call_predict(rmvpe: Any, audio: np.ndarray, sr: int) -> PredictionResult:
    predict = getattr(rmvpe, "predict", None)
    if predict is None or not callable(predict):
        public = [name for name in dir(rmvpe) if not name.startswith("_")]
        raise SmokeError(
            "RMVPE instance has no callable predict(...) method.\n"
            f"Public instance names: {public}\n"
            "Package API may have changed; inspect the installed package before integrating."
        )

    try:
        sig = inspect.signature(predict)
    except Exception:
        sig = inspect.Signature()

    params = sig.parameters
    attempts: list[tuple[str, dict[str, Any], tuple[Any, ...]]] = []

    # rmvpe-onnx README API: rmvpe.predict(audio=audio, sr=sr)
    if "audio" in params and "sr" in params:
        attempts.append(("predict(audio=audio, sr=sr)", {"audio": audio, "sr": sr}, ()))
    if "waveform" in params and "sr" in params:
        attempts.append(("predict(waveform=audio, sr=sr)", {"waveform": audio, "sr": sr}, ()))
    if "audio" in params and "sample_rate" in params:
        attempts.append(
            ("predict(audio=audio, sample_rate=sr)", {"audio": audio, "sample_rate": sr}, ())
        )

    # Conservative positional fallbacks for API variants, tried only after inspection-based calls.
    attempts.append(("predict(audio, sr)", {}, (audio, sr)))
    attempts.append(("predict(audio=audio)", {"audio": audio}, ()))

    errors: list[str] = []
    for label, kwargs, args in attempts:
        try:
            result = predict(*args, **kwargs)
            parsed = _parse_prediction_result(result)
            print(f"RMVPE call succeeded with {label}")
            return parsed
        except Exception as err:
            errors.append(f"{label} failed: {type(err).__name__}: {err}")

    raise SmokeError(
        "Could not call RMVPE.predict(...) using inspected API variants.\n"
        f"Signature: {sig}\n" + "\n".join(errors)
    )


def _parse_prediction_result(result: Any) -> PredictionResult:
    if isinstance(result, dict):
        time = result.get("time") or result.get("times") or result.get("t")
        frequency = result.get("frequency") or result.get("f0") or result.get("pitch")
        confidence = result.get("confidence") or result.get("conf") or result.get("uv")
        activation = result.get("activation") or result.get("activations")
    elif isinstance(result, (tuple, list)):
        if len(result) < 3:
            raise SmokeError(
                f"RMVPE result tuple/list has {len(result)} values; expected at least 3."
            )
        time = result[0]
        frequency = result[1]
        confidence = result[2]
        activation = result[3] if len(result) >= 4 else None
    else:
        raise SmokeError(f"Unsupported RMVPE result type: {type(result).__name__}")

    time_arr = np.asarray(time, dtype=np.float64).reshape(-1)
    freq_arr = np.asarray(frequency, dtype=np.float64).reshape(-1)
    conf_arr = np.asarray(confidence, dtype=np.float64).reshape(-1)

    if not (len(time_arr) == len(freq_arr) == len(conf_arr)):
        raise SmokeError(
            "RMVPE output arrays have inconsistent lengths: "
            f"time={len(time_arr)}, frequency={len(freq_arr)}, confidence={len(conf_arr)}"
        )
    if len(time_arr) == 0:
        raise SmokeError("RMVPE returned no frames.")

    activation_shape = list(np.asarray(activation).shape) if activation is not None else None
    return PredictionResult(
        time=time_arr,
        frequency=freq_arr,
        confidence=conf_arr,
        activation_shape=activation_shape,
    )


def _output_path_for(vocals_path: Path) -> Path:
    # Expected: .../analysis/stems/vocals.wav -> .../analysis/pitch/vocal_pitch.json
    if vocals_path.parent.name == "stems" and vocals_path.parent.parent.name == "analysis":
        pitch_dir = vocals_path.parent.parent / "pitch"
    else:
        # Generic fallback: same directory's ../pitch, matching the user's request.
        pitch_dir = vocals_path.parent / ".." / "pitch"
    return pitch_dir.resolve() / "vocal_pitch.json"


def _estimate_frame_period_ms(times: np.ndarray) -> float | None:
    if len(times) < 2:
        return None
    diffs = np.diff(times)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return None
    return float(np.median(diffs) * 1000.0)


def _safe_float(value: float) -> float | None:
    value = float(value)
    if math.isfinite(value):
        return value
    return None


def _write_pitch_json(
    *,
    out_path: Path,
    source_path: Path,
    sample_rate: int,
    prediction: PredictionResult,
    confidence_threshold: float,
    ort_providers: list[str],
    session_providers: list[list[str]],
    rmvpe_module_file: str,
    rmvpe_version: str | None,
    rmvpe_constructor_kwargs: dict[str, Any],
    rmvpe_constructor_signature: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    voiced_mask = (prediction.frequency > 0.0) & (prediction.confidence >= confidence_threshold)
    frames: list[dict[str, Any]] = []
    for t, f0, conf, voiced in zip(
        prediction.time,
        prediction.frequency,
        prediction.confidence,
        voiced_mask,
        strict=True,
    ):
        frames.append(
            {
                "time": round(float(t), 6),
                "f0_hz": _safe_float(f0),
                "confidence": _safe_float(conf),
                "voiced": bool(voiced),
            }
        )

    frame_period_ms = _estimate_frame_period_ms(prediction.time)
    duration_seconds = float(prediction.time[-1]) if len(prediction.time) else 0.0
    voiced_count = int(voiced_mask.sum())

    payload = {
        "backend": "rmvpe",
        "status": "ok",
        "source_audio_path": str(source_path),
        "sample_rate": sample_rate,
        "frame_period_ms": round(frame_period_ms, 6) if frame_period_ms is not None else None,
        "confidence_threshold": confidence_threshold,
        "frame_count": len(frames),
        "voiced_count": voiced_count,
        "voiced_ratio": round(voiced_count / len(frames), 6) if frames else 0.0,
        "duration_seconds": round(duration_seconds, 6),
        "frames": frames,
        "debug": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rmvpe_module_file": rmvpe_module_file,
            "rmvpe_version": rmvpe_version,
            "rmvpe_constructor_signature": rmvpe_constructor_signature,
            "rmvpe_constructor_kwargs": rmvpe_constructor_kwargs,
            "onnxruntime_available_providers": ort_providers,
            "rmvpe_session_provider_reports": session_providers,
            "activation_shape": prediction.activation_shape,
            "script": "scripts/smoke_rmvpe.py",
            "notes": [
                "Standalone smoke output only; no melody, no MIDI, no pYIN fallback.",
                "Frequency is raw RMVPE output. Use voiced/confidence flags for filtering.",
            ],
        },
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test RMVPE/rmvpe-onnx on a Demucs vocals.wav and write "
            "vocal_pitch.json."
        ),
    )
    parser.add_argument("vocals_wav", help="Path to Demucs vocals.wav")
    parser.add_argument(
        "--python",
        type=Path,
        default=RMVPE_PYTHON,
        help=f"RMVPE GPU Python executable; default {RMVPE_PYTHON}.",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Threshold used only to mark voiced frames; default {DEFAULT_CONFIDENCE_THRESHOLD}.",
    )
    parser.add_argument(
        "--allow-non-vocals-name",
        action="store_true",
        help="Allow input filename other than vocals.wav for local experiments.",
    )
    return parser.parse_args(argv)


def _run_in_gpu_python(args: argparse.Namespace, argv: list[str]) -> int | None:
    if os.environ.get(GPU_ENV_READY) == "1":
        return None
    gpu_python = args.python.expanduser()
    if not gpu_python.is_file():
        _die(f"RMVPE Python does not exist: {gpu_python}")

    environment = build_gpu_subprocess_env(mode="onnx", gpu_python=gpu_python)
    environment[GPU_ENV_READY] = "1"
    command = [str(gpu_python), str(Path(__file__).resolve()), *argv]
    completed = subprocess.run(command, check=False, env=environment)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    args = _parse_args(raw_argv)
    child_returncode = _run_in_gpu_python(args, raw_argv)
    if child_returncode is not None:
        return child_returncode

    vocals_path = Path(args.vocals_wav).expanduser().resolve()
    if not vocals_path.exists():
        _die(f"Input file does not exist: {vocals_path}")
    if not vocals_path.is_file():
        _die(f"Input path is not a file: {vocals_path}")
    if vocals_path.name != EXPECTED_INPUT_NAME and not args.allow_non_vocals_name:
        _die(
            f"Expected input filename {EXPECTED_INPUT_NAME!r}, got {vocals_path.name!r}. "
            "Use --allow-non-vocals-name only for local experiments."
        )
    if not (0.0 <= args.confidence_threshold <= 1.0):
        _die("--confidence-threshold must be between 0.0 and 1.0")

    try:
        ort, ort_providers = _require_cuda_execution_provider()
        rmvpe_cls, rmvpe_module_file, rmvpe_version = _import_rmvpe_class()
        audio, sr = _read_audio(vocals_path)
        rmvpe, ctor_kwargs, ctor_sig = _instantiate_rmvpe(rmvpe_cls)
        session_providers = _validate_session_uses_cuda(rmvpe)
        prediction = _call_predict(rmvpe, audio, sr)

        out_path = _output_path_for(vocals_path)
        _write_pitch_json(
            out_path=out_path,
            source_path=vocals_path,
            sample_rate=sr,
            prediction=prediction,
            confidence_threshold=float(args.confidence_threshold),
            ort_providers=ort_providers,
            session_providers=session_providers,
            rmvpe_module_file=rmvpe_module_file,
            rmvpe_version=rmvpe_version,
            rmvpe_constructor_kwargs=ctor_kwargs,
            rmvpe_constructor_signature=ctor_sig,
        )
    except SmokeError as err:
        _die(str(err))

    print(f"OK: wrote {out_path}")
    print(f"Frames: {len(prediction.time)}")
    voiced_ratio = float(
        ((prediction.frequency > 0) & (prediction.confidence >= args.confidence_threshold)).mean()
    )
    print(f"Voiced ratio: {voiced_ratio:.3f}")
    print(f"ONNX Runtime providers: {ort_providers}")
    if session_providers:
        print(f"RMVPE session providers: {session_providers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
