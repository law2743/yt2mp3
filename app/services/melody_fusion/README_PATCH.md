# yt2mp3 Melody Fusion Notes

Adaptive melody fusion is now integrated in this repo under `app/services/melody_fusion/`.

Current behavior:

- Reads standardized backend CSV files.
- Writes `analysis/melody/fusion/fusion.csv`.
- Writes `analysis/melody/fusion/fusion.json`.
- Writes `analysis/melody/fusion/diagnostics.json`.
- Does not import torch, onnxruntime, Demucs, RMVPE, or GPU runtimes.
- Requires at least two successful pitch backends; it does not fallback to RMVPE-only melody output.

See `docs/melody_fusion.md` for the current CLI and artifact contract.
