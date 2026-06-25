# yt2mp3 docs

This directory keeps project notes that are useful for development but do not
need to live at the repository root.

## Files

* `gpu.md`: host-native GPU venv, Demucs / RMVPE subprocess rules, smoke tests,
  and GPU troubleshooting notes.
* `yt2mp3-spec.md`: historical MVP product and technical specification. The root
  `README.md` is the source of truth for the current deployment model and active
  roadmap.

Files that stay at the repository root are there because tools or conventions
expect them there: `README.md`, `LICENSE`, `pyproject.toml`,
`requirements*.txt`, `.env.example`, and deployment config.

## Documentation consistency rules

Keep the root `README.md` focused on how to use, run, deploy, and test the
project. Keep implementation details in `docs/`.

When changing GPU behavior, update both:

* Root `README.md`: user-facing summary, required settings, limitations, and
  roadmap status.
* `docs/gpu.md`: venv rebuild steps, subprocess environment rules, smoke tests,
  and backend-specific details.

The following details must stay consistent across docs:

* Host GPU runtime path:
  `/home/startech/venvs/yt2mp3-gpu/bin/python`
* Main FastAPI environment stays lightweight and must not install or import
  Demucs, PyTorch, ONNX Runtime GPU, RMVPE, or CUDA packages.
* GPU tools are called only through subprocesses using
  `build_gpu_subprocess_env(...)`.
* `DEMUCS_PYTHON` and `RMVPE_PYTHON` should point to the same host GPU venv
  unless a future design explicitly separates them.
* The local backend runs host-native in WSL. Docker is not maintained as a local
  backend deployment path.
* Phase 2 vocal pitch and melody preview are RMVPE-only. CPU pitch fallback
  must not be used to create fake `analysis/pitch/vocal_pitch.json` or melody
  artifacts.
* `analysis/pitch/vocal_pitch.json` should only represent a real RMVPE vocal
  pitch result from `analysis/stems/vocals.wav`.

When changing artifact paths, update all relevant docs and tests together:

* `analysis/stems/vocals.wav`
* `analysis/stems/accompaniment.wav`
* `analysis/stems/metadata.json`
* `analysis/pitch/vocal_pitch.json`
* `analysis/melody/*.json`
* `analysis/melody/*.mid`

When changing dependencies:

* Keep production dependencies in `requirements.txt`.
* Keep development dependencies in `requirements-dev.txt`.
* Keep host GPU dependencies in `requirements-gpu.txt`.
* Do not add GPU dependencies to the main FastAPI runtime.
* Update `THIRD_PARTY_NOTICES.md` if dependency or license coverage changes.

Before merging documentation-only changes, run at least:

```bash
ruff check app tests
pytest -q
```

If the change touches GPU runtime behavior, also run the relevant smoke test:

```bash
python scripts/smoke_demucs.py /path/to/short_mix.wav
python scripts/smoke_rmvpe.py /path/to/analysis/stems/vocals.wav
```
