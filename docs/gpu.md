# Host GPU runtime

Demucs / PyTorch and RMVPE / ONNX Runtime are installed in one host-native GPU venv. The main FastAPI environment stays lightweight and calls this venv only through subprocesses.

This document owns the engineering details for Host GPU mode. The README should only describe the user-facing setup and link here for implementation details.

## Runtime contract

- The main FastAPI environment must not install or import Demucs, PyTorch, ONNX Runtime GPU, RMVPE, CUDA wheels, model weights, or GPU caches.
- The production Docker image must not install `requirements-gpu.txt`.
- Host GPU mode is for WSL / NVIDIA host-native FastAPI. It is not supported inside the current Docker deployment.
- Every GPU subprocess must use `build_gpu_subprocess_env(mode, gpu_python)` from `app/services/gpu_subprocess_env.py`.
- The host shell, Conda, or another virtualenv must not leak CUDA-related paths into GPU subprocesses.

## GPU venv

Default interpreter path:

```text
/home/startech/venvs/yt2mp3-gpu/bin/python
```

Both GPU settings should normally point to the same interpreter:

```dotenv
DEMUCS_PYTHON=/home/startech/venvs/yt2mp3-gpu/bin/python
RMVPE_PYTHON=/home/startech/venvs/yt2mp3-gpu/bin/python
RMVPE_TIMEOUT_SECONDS=300
RMVPE_VOICED_CONFIDENCE_THRESHOLD=0.03
```

The main FastAPI environment may be a separate Conda env or venv. Do not activate the GPU venv to run the FastAPI app itself.

## Rebuild the GPU venv

```bash
python3.12 -m venv /home/startech/venvs/yt2mp3-gpu
/home/startech/venvs/yt2mp3-gpu/bin/python -m pip install --upgrade pip
/home/startech/venvs/yt2mp3-gpu/bin/python -m pip install -r requirements-gpu.txt
```

`requirements-gpu.txt` pins the currently verified GPU environment. Keep it separate from `requirements.txt`; the production Docker image must not install Demucs, PyTorch, RMVPE, ONNX Runtime, or CUDA packages.

When GPU dependencies change, verify both Demucs and RMVPE smoke tests before updating README, `.env.example`, `requirements-gpu.txt`, this file, and `THIRD_PARTY_NOTICES.md` if needed.

## Environment isolation helper

All GPU subprocess users must call:

```python
build_gpu_subprocess_env(mode, gpu_python)
```

Do not hand-write adapter-specific CUDA cleanup. Add new GPU subprocess users to the shared helper instead.

### PyTorch / Demucs mode

Call:

```python
build_gpu_subprocess_env(mode="torch", gpu_python=DEMUCS_PYTHON)
```

Torch mode removes both `PYTHONPATH` and `LD_LIBRARY_PATH`, then leaves `LD_LIBRARY_PATH` unset. This lets the PyTorch wheel in the GPU venv resolve its own bundled CUDA and cuDNN libraries instead of loading an older host or Conda copy.

### ONNX Runtime / RMVPE mode

Call:

```python
build_gpu_subprocess_env(mode="onnx", gpu_python=RMVPE_PYTHON)
```

ONNX mode also removes external `PYTHONPATH` and `LD_LIBRARY_PATH`. It then runs a discovery script with `gpu_python` itself and scans that interpreter's `site-packages/nvidia/*/lib` directories. The discovered paths become the new clean `LD_LIBRARY_PATH`.

Discovery must run under `gpu_python`; the FastAPI venv may not have the same packages, Python version, or CUDA wheel layout.

## Demucs stem separation

Demucs is invoked through the GPU venv and publishes normalized artifacts under:

```text
analysis/stems/
  vocals.wav
  accompaniment.wav
  metadata.json
```

Demucs / torch failures must not create fake `vocals.wav` or `accompaniment.wav`. If stem separation fails, the backend should write explicit metadata / status and leave downstream logic to decide whether mix-based preview can continue.

`DEMUCS_CLEAN_ENV=true` is required when Demucs is enabled. The flag is retained as a legacy safety setting, but the subprocess environment should always be built by the shared helper.

## RMVPE vocal pitch backend

The formal Phase 2Cb vocal pitch backend executes:

```text
RMVPE_PYTHON scripts/run_rmvpe_pitch.py analysis/stems/vocals.wav <tmp-json>
```

The main FastAPI venv validates and normalizes the subprocess output before atomically publishing:

```text
analysis/pitch/vocal_pitch.json
```

Phase 2Cb is RMVPE-only:

- Input is `analysis/stems/vocals.wav`.
- Successful output uses `backend="rmvpe_onnx"` and `fallback_used=false`.
- If RMVPE fails for a vocals source, the melody request fails with `PITCH_FAILED` or equivalent explicit status.
- It must not fall back to pYIN.
- It must not create a fake pitch artifact.

pYIN remains part of the Phase 2A CPU-only melody preview path and should continue writing preview artifacts such as `analysis/melody/mix_pyin.json` or `analysis/melody/vocals_pyin.json`; it should not write `analysis/pitch/vocal_pitch.json`.

## RMVPE confidence and voiced semantics

`rmvpe-onnx` returns frame-level time, frequency, confidence, and activation. Store the normalized frame-level result in `vocal_pitch.json`.

Recommended behavior:

- `confidence` comes from rmvpe-onnx and is interpreted as voicing confidence in the range `0..1`.
- `voiced = confidence >= RMVPE_VOICED_CONFIDENCE_THRESHOLD`.
- Default `RMVPE_VOICED_CONFIDENCE_THRESHOLD=0.03`.
- Preserve raw `frequency_hz` from rmvpe-onnx even when `voiced=false`.
- Downstream steps must use `voiced` or `confidence`, not `frequency_hz` alone, to decide whether a frame is valid.
- Do not store full activation in `vocal_pitch.json` by default; record `activation_shape` in metadata if useful.

Minimal artifact shape:

```json
{
  "schema_version": "vocal_pitch.v1",
  "backend": "rmvpe_onnx",
  "fallback_used": false,
  "input_source": "vocals",
  "sample_rate": 16000,
  "duration_seconds": 123.45,
  "frame_hz": 100,
  "hop_seconds": 0.01,
  "voiced_confidence_threshold": 0.03,
  "points": [
    {
      "time": 1.23,
      "frequency_hz": 261.63,
      "midi": 60.0,
      "confidence": 0.92,
      "voiced": true
    }
  ],
  "metadata": {
    "model": "rmvpe-onnx",
    "device": "cuda",
    "confidence_source": "rmvpe_onnx",
    "activation_shape": [12345, 360],
    "created_at": "2026-06-24T00:00:00Z"
  }
}
```

## Smoke tests

Run smoke tests from the main project environment, not from inside the GPU venv:

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav
```

The smoke scripts build clean subprocess environments through `app/services/gpu_subprocess_env.py`.

A passing main test suite such as `pytest -q` validates FastAPI, API, pipeline, artifact, and non-GPU logic. It does not replace host GPU smoke tests, because GPU dependencies intentionally live outside the main environment.

## Verified environment

Keep the exact verified versions in this section when GPU dependencies change.

Current verified WSL GPU setup:

- Python 3.12.3 in `/home/startech/venvs/yt2mp3-gpu`
- PyTorch / torchaudio 2.11.0+cu128
- TorchCodec 0.11.0 CPU wheel
- Demucs 4.0.1
- ONNX Runtime GPU 1.26.0
- rmvpe-onnx 0.2.3
- NVIDIA GeForce RTX 5070

TorchCodec uses the CPU wheel only for audio output; Demucs inference still runs through PyTorch CUDA.

## Troubleshooting checklist

When GPU subprocesses fail:

1. Confirm the main FastAPI env is not the GPU venv.
2. Confirm `DEMUCS_PYTHON` and `RMVPE_PYTHON` point to `/home/startech/venvs/yt2mp3-gpu/bin/python` or the intended GPU venv.
3. Run `nvidia-smi` from WSL.
4. Run Demucs and RMVPE smoke tests from the main project environment.
5. Confirm `PYTHONPATH` and external `LD_LIBRARY_PATH` are not leaking into subprocesses.
6. Confirm ONNX Runtime reports `CUDAExecutionProvider` inside the GPU venv.

## Rules

- Do not pass through an existing `PYTHONPATH` to GPU subprocesses.
- Do not pass through an existing `LD_LIBRARY_PATH` to GPU subprocesses.
- Do not hand-write adapter-specific CUDA environment cleanup.
- Do not import GPU packages in the main FastAPI env.
- Do not install `requirements-gpu.txt` in the production Docker image.
- Do not create fake stems or fake pitch artifacts.
- Add new GPU subprocess users to the shared helper instead.
