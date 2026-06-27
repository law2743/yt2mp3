# Adaptive Melody Fusion

Adaptive melody fusion lives inside this repo under `app/services/melody_fusion/`.
It is not a separate package or repository.

The module only reads standardized frame-level pitch CSV files and writes lightweight
fusion artifacts. It must not import Demucs, RMVPE, torch, onnxruntime, or any GPU
runtime. GPU pitch backends run in subprocess scripts and publish CSV artifacts first.

## Standard Input CSV

Every backend writes the same columns:

```text
time_sec,f0_hz,raw_f0_hz,confidence,confidence_kind,voiced,backend
```

- `time_sec`: frame time in seconds.
- `f0_hz`: post-processed f0 used by fusion.
- `raw_f0_hz`: original backend f0 when available; otherwise same as `f0_hz`.
- `confidence`: backend confidence-like score, blank when unavailable.
- `confidence_kind`: `voicing`, `periodicity`, `peak_probability`, or `none`.
- `voiced`: `1` or `0`.
- `backend`: `rmvpe`, `torchcrepe`, `fcpe`, or `pesto`.

Missing confidence is allowed. Fusion still uses that backend's f0 candidates,
agreement with other models, and transition scoring. Missing confidence is not
replaced with a fake neutral score.

## Job Artifacts

Fusion artifacts are written under:

```text
job_id/
  analysis/
    melody/
      fusion/
        inputs/
          rmvpe.csv
          torchcrepe.csv
          fcpe.csv
          pesto.csv
        fusion.csv
        fusion.json
        diagnostics.json
```

The legacy RMVPE artifact remains available at `analysis/pitch/vocal_pitch.json`.
It is used as a cache/source for `inputs/rmvpe.csv`, but adaptive fusion success
publishes the formal compatible outputs:

```text
analysis/melody.json
analysis/melody.mid
```

If fusion fails, these compatible outputs are not overwritten by an RMVPE fallback.

## CLI

From the repo root:

```bash
python -m app.services.melody_fusion.cli fuse \
  --rmvpe job_id/analysis/melody/fusion/inputs/rmvpe.csv \
  --torchcrepe job_id/analysis/melody/fusion/inputs/torchcrepe.csv \
  --fcpe job_id/analysis/melody/fusion/inputs/fcpe.csv \
  --pesto job_id/analysis/melody/fusion/inputs/pesto.csv \
  --out job_id/analysis/melody/fusion/fusion.json \
  --csv-out job_id/analysis/melody/fusion/fusion.csv \
  --diagnostics-out job_id/analysis/melody/fusion/diagnostics.json
```

At least two backend CSVs are required.

## Local WAV Debug

You can run all available pitch backends against a chosen WAV and compare each
backend with the fused result:

```bash
python scripts/run_melody_fusion_debug.py /mnt/d/vocals-1.wav \
  --out-dir /tmp/yt2mp3-fusion-debug
```

The debug script writes:

```text
/tmp/yt2mp3-fusion-debug/
  vocals_mono_16000.wav
  inputs/
    rmvpe.csv
    torchcrepe.csv
    fcpe.csv
    pesto.csv
  fusion.csv
  fusion.json
  comparison.csv
  diagnostics.json
```

`comparison.csv` contains per-frame f0 and cents deltas from each backend to
the fused pitch. `diagnostics.json` contains backend success/failure state and
summary agreement metrics.

## Diagnostics

`diagnostics.json` records:

- `fusion_status`
- `required_min_successful_backends`
- `succeeded_backends`
- `missing_backends`
- `failed_backends`
- backend input paths, row counts, confidence kind, missing confidence rows, voiced ratio
- fusion warnings and model weights when fusion succeeds

If fewer than two backends succeed, diagnostics uses:

```json
{
  "fusion_status": "failed",
  "failed_reason": "not_enough_successful_backends",
  "required_min_successful_backends": 2
}
```

## Pipeline Boundary

FastAPI uses `app/services/pipelines/melody_fusion_pipeline.py` to orchestrate:

1. Create/reuse `analysis/melody/fusion/vocals_mono_16000.wav`.
2. Generate backend CSVs.
3. Run adaptive fusion.
4. Convert `fusion.json` into compatible `melody.json` and `melody.mid`.

The frontend continues to consume the existing melody JSON response shape.
