# Host GPU runtime

Demucs / PyTorch and RMVPE / ONNX Runtime are installed in one host-native GPU venv. The main FastAPI environment stays lightweight and calls this venv only through subprocesses.

This document owns the engineering details for Host GPU mode. The README should only describe the user-facing setup and link here for implementation details.

## Runtime contract

- The main FastAPI environment must not install or import Demucs, PyTorch, ONNX Runtime GPU, RMVPE, CUDA wheels, model weights, or GPU caches.
- Host GPU mode is for WSL / NVIDIA host-native FastAPI. The project does not maintain a local Docker backend deployment path.
- Every production GPU subprocess must use `build_gpu_subprocess_env(mode, gpu_python)` from `app/services/gpu_subprocess_env.py`.
- The host shell, Conda, or another virtualenv must not leak CUDA-related paths into GPU subprocesses.
- Experimental pitch smoke scripts may be run manually from the GPU venv, but production FastAPI code must not import those GPU packages directly.

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

`requirements-gpu.txt` pins the verified GPU environment. Keep it separate from `requirements.txt`; the main FastAPI runtime must not install Demucs, PyTorch, RMVPE, ONNX Runtime, CUDA packages, or experimental pitch backends.

When GPU dependencies change, verify Demucs, RMVPE, and any experimental pitch smoke tests before updating README, `.env.example`, `requirements-gpu.txt`, this file, and `THIRD_PARTY_NOTICES.md` if needed.

## Environment isolation helper

All production GPU subprocess users must call:

```python
build_gpu_subprocess_env(mode, gpu_python)
```

Do not hand-write adapter-specific CUDA cleanup. Add new production GPU subprocess users to the shared helper instead.

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

For manual smoke tests, use the same isolation pattern from the shell:

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH python <script.py> ...
```

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

The legacy RMVPE artifact is still preserved for compatibility/cache:

- Input is `analysis/stems/vocals.wav`.
- Successful output uses `backend="rmvpe_onnx"` and `fallback_used=false`.
- It must not fall back to a CPU pitch backend.
- It must not create a fake pitch artifact.

The formal melody preview path now writes adaptive fusion artifacts under `analysis/melody/fusion/`
and publishes compatible outputs as `analysis/melody.json` and `analysis/melody.mid`.
If fewer than two pitch backends succeed, fusion fails explicitly and must not silently fall back
to the RMVPE-only melody path.

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

## Experimental pitch backends

These backends are for comparison and confidence analysis only. They are not the formal Phase 2Cb default.

- RMVPE remains the production/default vocal pitch backend.
- FCPE, PESTO, and torchcrepe must stay in the host-native GPU venv.
- Do not install them in the main FastAPI venv, production Docker image, or Render frontend environment.
- Do not use simple majority voting when obvious harmony exists. Prefer confidence labels and review-needed segment flags.

### Install experimental backends safely

Activate the GPU venv:

```bash
source /home/startech/venvs/yt2mp3-gpu/bin/activate
```

Confirm the verified PyTorch stack:

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH python - <<'PY'
import torch, torchaudio
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available())
print("torchaudio:", torchaudio.__version__)
PY
```

Expected:

```text
torch: 2.11.0+cu128
cuda: True
torchaudio: 2.11.0+cu128
```

Install FCPE without allowing pip to replace the verified PyTorch/CUDA stack:

```bash
pip install torchfcpe==0.0.4 --no-deps
pip install local_attention pydub pretty_midi --no-deps
pip install hyper-connections torch-einops-utils --no-deps
```

Install PESTO without allowing pip to replace the verified PyTorch/CUDA stack:

```bash
pip install pesto-pitch==2.0.1 --no-deps
```

Install torchcrepe without allowing pip to replace the verified PyTorch/CUDA stack:

```bash
pip install torchcrepe==0.0.24 --no-deps
pip install resampy==0.4.3 --no-deps
```

After installing, verify that `torch` and `torchaudio` were not changed:

```bash
python -m pip freeze > /tmp/yt2mp3-gpu.after_pitch_backends.txt
grep -Ei "^(torch|torchaudio|torchvision|torchcrepe|pesto-pitch|torchfcpe|resampy)==" /tmp/yt2mp3-gpu.after_pitch_backends.txt
```

## Smoke tests

Run formal Demucs and RMVPE smoke tests from the main project environment, not from inside the GPU venv:

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav
```

The repo smoke scripts should build clean subprocess environments through `app/services/gpu_subprocess_env.py`.

Run experimental pitch smoke tests manually from the GPU venv:

```bash
source /home/startech/venvs/yt2mp3-gpu/bin/activate
```

### FCPE smoke

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH \
python scripts/smoke_fcpe.py \
  /path/to/analysis/stems/vocals.wav \
  /tmp/fcpe_pitch.json \
  --threshold 0.03
```

### PESTO smoke

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH \
python scripts/smoke_pesto.py \
  /path/to/analysis/stems/vocals.wav \
  /tmp/pesto_pitch.json \
  --confidence-threshold 0.20
```

### torchcrepe smoke

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH \
python scripts/smoke_torchcrepe.py \
  /path/to/analysis/stems/vocals.wav \
  /tmp/torchcrepe_pitch.json \
  --periodicity-threshold 0.60
```

### Four-backend ensemble smoke

This script runs FCPE, RMVPE, PESTO, and torchcrepe, then writes normalized backend JSON files plus a comparison report.

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH \
python scripts/smoke_pitch_ensemble.py \
  /path/to/analysis/stems/vocals.wav \
  --out-dir /tmp/pitch-ensemble-smoke \
  --rmvpe-script scripts/smoke_rmvpe.py
```

Outputs:

```text
/tmp/pitch-ensemble-smoke/
  fcpe_pitch.json
  rmvpe_pitch.json
  pesto_pitch.json
  torchcrepe_pitch.json
  pitch_ensemble_report.json
  pitch_ensemble_report.md
```

If the repo RMVPE smoke script is unavailable, run the remaining experimental backends:

```bash
env -u LD_LIBRARY_PATH -u PYTHONPATH \
python scripts/smoke_pitch_ensemble.py \
  /path/to/analysis/stems/vocals.wav \
  --out-dir /tmp/pitch-ensemble-smoke \
  --skip-rmvpe
```

## Experimental comparison conclusions

Observed on one full-length Demucs `vocals.wav` with clear female lead vocal and obvious harmony:

- FCPE and RMVPE can agree strongly on clean sections.
- RMVPE sometimes jumps to a very high line, likely high harmony, overtone, or octave-related behavior.
- PESTO and torchcrepe frequently agree with each other, which suggests they are not random noise; they may be tracking a stable alternate voice line.
- Some segments form a clear two-pair conflict: `FCPE/RMVPE` versus `PESTO/torchcrepe`.
- In harmony-heavy sections, simple majority voting is unsafe.

Recommended confidence labels:

| Label | Meaning | Recommended use |
|---|---|---|
| `high_confidence_3plus_agree` | At least three models agree within 0.5 semitone. | Safe candidate for automatic melody/MIDI. |
| `harmony_conflict_2v2` | FCPE/RMVPE agree while PESTO/torchcrepe agree on another line. | Review-needed; do not pick by majority. |
| `rmvpe_high_outlier` | RMVPE is much higher than PESTO/torchcrepe. | Possible high harmony/overtone; review-needed. |
| `low_harmony_or_alt_voice` | FCPE/RMVPE agree while PESTO/torchcrepe are much lower. | Possible lower harmony or alternate voice; review-needed. |
| `pesto_crepe_agree_candidate` | PESTO and torchcrepe agree without enough support from FCPE/RMVPE. | Candidate only; not high confidence. |
| `fcpe_rmvpe_agree_candidate` | FCPE and RMVPE agree without enough support from PESTO/torchcrepe. | Candidate only; not high confidence. |
| `single_model_voiced` | Only one model is voiced. | Very low confidence; do not publish as formal melody. |
| `all_unvoiced` | No model is voiced. | Silence/unvoiced. |
| `low_confidence_disagree` | Models disagree without a stable rule. | Do not publish as formal melody. |

Default experimental thresholds used during smoke tests:

```text
FCPE threshold: 0.03
PESTO confidence threshold: 0.20
torchcrepe periodicity threshold: 0.60
Model agreement tolerance: 0.5 semitone
High-outlier threshold: 12 semitones
```

These values are not production defaults. They are starting points for comparison reports.

## Verified environment

Keep the exact verified versions in this section when GPU dependencies change.

Current verified WSL GPU setup:

- Python 3.12.3 in `/home/startech/venvs/yt2mp3-gpu`
- PyTorch / torchaudio 2.11.0+cu128
- TorchCodec 0.11.0 CPU wheel
- Demucs 4.0.1
- ONNX Runtime GPU 1.26.0
- rmvpe-onnx 0.2.3
- torchfcpe 0.0.4
- pesto-pitch 2.0.1
- torchcrepe 0.0.24
- resampy 0.4.3
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
7. For FCPE / torchcrepe errors, confirm PyTorch still reports `cuda=True` inside the GPU venv.
8. For PESTO errors, confirm the `pesto` CLI exists in the GPU venv PATH.

## Rules

- Do not pass through an existing `PYTHONPATH` to GPU subprocesses.
- Do not pass through an existing `LD_LIBRARY_PATH` to GPU subprocesses.
- Do not hand-write adapter-specific CUDA environment cleanup in production code.
- Do not import GPU packages in the main FastAPI env.
- Do not install `requirements-gpu.txt` in the main FastAPI env.
- Do not create fake stems or fake pitch artifacts.
- Do not use simple pitch majority voting as formal melody output when harmony conflict is detected.
- Add new production GPU subprocess users to the shared helper instead.
