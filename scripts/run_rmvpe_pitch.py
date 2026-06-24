#!/usr/bin/env python3
"""Extract vocal pitch with rmvpe-onnx.

This script is executed by the configured RMVPE_PYTHON GPU venv. It intentionally
does not import the FastAPI application or any app.* modules.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import sys
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Iterable

import numpy as np


class PitchError(RuntimeError):
    pass


def _die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _require_cuda_execution_provider() -> None:
    try:
        ort = import_module("onnxruntime")
    except Exception as err:
        raise PitchError(f"Cannot import onnxruntime: {err}") from err
    providers = list(getattr(ort, "get_available_providers")())
    if "CUDAExecutionProvider" not in providers:
        raise PitchError(
            "onnxruntime CUDAExecutionProvider is unavailable; "
            f"available providers: {providers}"
        )


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        sf = import_module("soundfile")
    except Exception as err:
        raise PitchError(f"Cannot import soundfile: {err}") from err
    try:
        audio, sample_rate = sf.read(str(path), always_2d=False, dtype="float32")
    except Exception as err:
        raise PitchError(f"Failed to read audio: {err}") from err
    waveform = np.asarray(audio, dtype=np.float32)
    if waveform.ndim == 2:
        waveform = waveform.mean(axis=1, dtype=np.float32)
    elif waveform.ndim != 1:
        raise PitchError(f"Unsupported audio shape {waveform.shape}; expected mono or stereo WAV")
    if waveform.size == 0:
        raise PitchError("Audio is empty")
    if not np.isfinite(waveform).all():
        raise PitchError("Audio contains NaN or Inf values")
    return waveform, int(sample_rate)


def _import_rmvpe_class() -> type[Any]:
    try:
        module = import_module("rmvpe_onnx")
    except Exception as err:
        raise PitchError(f"Cannot import rmvpe_onnx: {err}") from err
    rmvpe_class = getattr(module, "RMVPE", None)
    if rmvpe_class is None or not inspect.isclass(rmvpe_class):
        public = [name for name in dir(module) if not name.startswith("_")]
        raise PitchError(f"rmvpe_onnx.RMVPE was not found; public names: {public}")
    return rmvpe_class


def _candidate_constructor_kwargs(sig: inspect.Signature) -> list[dict[str, Any]]:
    params = sig.parameters
    attempts: list[dict[str, Any]] = []
    for key in ("providers", "execution_providers"):
        if key in params:
            attempts.append({key: ["CUDAExecutionProvider"]})
    for key in ("provider", "execution_provider"):
        if key in params:
            attempts.append({key: "CUDAExecutionProvider"})
    if "device" in params:
        attempts.append({"device": "cuda"})
    attempts.append({})
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for kwargs in attempts:
        marker = json.dumps(kwargs, sort_keys=True)
        if marker not in seen:
            unique.append(kwargs)
            seen.add(marker)
    return unique


def _instantiate_rmvpe(cls: type[Any]) -> Any:
    try:
        sig = inspect.signature(cls)
    except Exception:
        sig = inspect.Signature()
    errors: list[str] = []
    for kwargs in _candidate_constructor_kwargs(sig):
        try:
            return cls(**kwargs)
        except Exception as err:
            errors.append(f"RMVPE({kwargs}) failed: {type(err).__name__}: {err}")
    raise PitchError("Could not instantiate rmvpe_onnx.RMVPE. " + " ".join(errors))


def _iter_possible_sessions(obj: Any) -> Iterable[Any]:
    seen: set[int] = set()
    stack = [obj]
    while stack:
        current = stack.pop()
        obj_id = id(current)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        if hasattr(current, "get_providers") and callable(current.get_providers):
            yield current
            continue
        for name in ("session", "sess", "ort_session", "model", "net", "predictor"):
            child = getattr(current, name, None)
            if child is not None:
                stack.append(child)


def _require_session_cuda(rmvpe: Any) -> None:
    reports: list[list[str]] = []
    for session in _iter_possible_sessions(rmvpe):
        try:
            reports.append(list(session.get_providers()))
        except Exception:
            continue
    if reports and not any("CUDAExecutionProvider" in providers for providers in reports):
        raise PitchError(f"RMVPE session is not using CUDAExecutionProvider: {reports}")


def _call_predict(rmvpe: Any, audio: np.ndarray, sample_rate: int) -> tuple[Any, Any, Any, Any]:
    predict = getattr(rmvpe, "predict", None)
    if predict is None or not callable(predict):
        raise PitchError("RMVPE instance has no callable predict method")
    try:
        sig = inspect.signature(predict)
    except Exception:
        sig = inspect.Signature()
    params = sig.parameters
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    if "audio" in params and "sr" in params:
        attempts.append(((), {"audio": audio, "sr": sample_rate}))
    if "waveform" in params and "sr" in params:
        attempts.append(((), {"waveform": audio, "sr": sample_rate}))
    if "audio" in params and "sample_rate" in params:
        attempts.append(((), {"audio": audio, "sample_rate": sample_rate}))
    attempts.append(((audio, sample_rate), {}))
    attempts.append(((), {"audio": audio}))

    errors: list[str] = []
    for args, kwargs in attempts:
        try:
            result = predict(*args, **kwargs)
            return _parse_prediction(result)
        except Exception as err:
            errors.append(f"{type(err).__name__}: {err}")
    raise PitchError("Could not call RMVPE.predict. " + " ".join(errors))


def _parse_prediction(result: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    if isinstance(result, dict):
        time = result.get("time") or result.get("times") or result.get("t")
        frequency = result.get("frequency") or result.get("f0") or result.get("pitch")
        confidence = result.get("confidence") or result.get("conf") or result.get("uv")
        activation = result.get("activation") or result.get("activations")
    elif isinstance(result, (tuple, list)):
        if len(result) < 3:
            raise PitchError(f"RMVPE returned {len(result)} values; expected at least 3")
        time, frequency, confidence = result[:3]
        activation = result[3] if len(result) >= 4 else None
    else:
        raise PitchError(f"Unsupported RMVPE result type: {type(result).__name__}")

    time_arr = np.asarray(time, dtype=np.float64).reshape(-1)
    freq_arr = np.asarray(frequency, dtype=np.float64).reshape(-1)
    conf_arr = np.asarray(confidence, dtype=np.float64).reshape(-1)
    activation_arr = np.asarray(activation) if activation is not None else None
    if not (len(time_arr) == len(freq_arr) == len(conf_arr)):
        raise PitchError(
            "RMVPE output arrays have inconsistent lengths: "
            f"time={len(time_arr)}, frequency={len(freq_arr)}, confidence={len(conf_arr)}"
        )
    if len(time_arr) == 0:
        raise PitchError("RMVPE returned no frames")
    return time_arr, freq_arr, conf_arr, activation_arr


def _safe_float(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def _midi_from_frequency(value: float) -> float | None:
    frequency = _safe_float(value)
    if frequency is None or frequency <= 0:
        return None
    return 69.0 + 12.0 * math.log2(frequency / 440.0)


def _hop_seconds(times: np.ndarray) -> float:
    if len(times) < 2:
        return 1.0
    diffs = np.diff(times)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return 1.0
    return float(np.median(diffs))


def _write_output(
    output: Path,
    *,
    sample_rate: int,
    duration_seconds: float,
    threshold: float,
    time: np.ndarray,
    frequency: np.ndarray,
    confidence: np.ndarray,
    activation: np.ndarray | None,
) -> None:
    hop_seconds = _hop_seconds(time)
    points = []
    for t, freq, conf in zip(time, frequency, confidence, strict=True):
        confidence_value = min(1.0, max(0.0, float(conf))) if math.isfinite(float(conf)) else 0.0
        points.append(
            {
                "time": round(float(t), 6),
                "frequency_hz": _safe_float(freq),
                "midi": (
                    round(midi, 6)
                    if (midi := _midi_from_frequency(float(freq))) is not None
                    else None
                ),
                "confidence": round(confidence_value, 6),
                "voiced": confidence_value >= threshold,
            }
        )
    payload = {
        "schema_version": "vocal_pitch.v1",
        "backend": "rmvpe_onnx",
        "fallback_used": False,
        "input_source": "vocals",
        "sample_rate": sample_rate,
        "duration_seconds": round(duration_seconds, 6),
        "frame_hz": round(1.0 / hop_seconds, 6),
        "hop_seconds": round(hop_seconds, 6),
        "voiced_confidence_threshold": threshold,
        "points": points,
        "metadata": {
            "model": "rmvpe-onnx",
            "device": "cuda",
            "confidence_source": "rmvpe_onnx",
            "activation_shape": list(activation.shape) if activation is not None else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract RMVPE vocal pitch from vocals.wav")
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--voiced-confidence-threshold", type=float, default=0.03)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not 0 <= args.voiced_confidence_threshold <= 1:
        _die("--voiced-confidence-threshold must be between 0 and 1")
    input_wav = args.input_wav.resolve()
    if not input_wav.is_file():
        _die(f"Input WAV not found: {input_wav}")
    try:
        _require_cuda_execution_provider()
        audio, sample_rate = _read_audio(input_wav)
        rmvpe_class = _import_rmvpe_class()
        rmvpe = _instantiate_rmvpe(rmvpe_class)
        _require_session_cuda(rmvpe)
        time, frequency, confidence, activation = _call_predict(rmvpe, audio, sample_rate)
        _write_output(
            args.output_json,
            sample_rate=sample_rate,
            duration_seconds=audio.size / sample_rate,
            threshold=float(args.voiced_confidence_threshold),
            time=time,
            frequency=frequency,
            confidence=confidence,
            activation=activation,
        )
    except PitchError as err:
        _die(str(err))
    print(f"OK: wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
