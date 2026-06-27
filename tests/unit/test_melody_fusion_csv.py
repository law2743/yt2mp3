from __future__ import annotations

from pathlib import Path

from app.services.melody_fusion import FusionConfig, fuse_pitch_csvs
from app.services.melody_fusion.io import write_pitch_csv


def _write_pitch(
    path: Path,
    backend: str,
    values: list[float],
    *,
    confidence_kind: str = "voicing",
) -> None:
    rows = []
    for i, f0 in enumerate(values):
        voiced = f0 > 0
        row = {
            "time_sec": round(i * 0.01, 2),
            "f0_hz": f0 if voiced else 0.0,
            "raw_f0_hz": f0 if f0 > 0 else 220.0,
            "voiced": voiced,
            "backend": backend,
            "confidence_kind": confidence_kind,
        }
        row["confidence"] = None if confidence_kind == "none" else (0.9 if voiced else 0.01)
        rows.append(row)
    write_pitch_csv(path, rows)


def test_fusion_prefers_consensus_over_spike(tmp_path: Path) -> None:
    # 3 models stay at A4; one model spikes to E5 for one frame.
    a4 = 440.0
    e5 = 659.25
    values_good = [a4] * 20
    values_spike = [a4] * 10 + [e5] + [a4] * 9
    rmvpe = tmp_path / "rmvpe.csv"
    torchcrepe = tmp_path / "torchcrepe.csv"
    fcpe = tmp_path / "fcpe.csv"
    pesto = tmp_path / "pesto.csv"
    _write_pitch(rmvpe, "rmvpe", values_good)
    _write_pitch(torchcrepe, "torchcrepe", values_spike, confidence_kind="periodicity")
    _write_pitch(fcpe, "fcpe", values_good, confidence_kind="none")
    _write_pitch(pesto, "pesto", values_good)

    out = fuse_pitch_csvs(
        rmvpe_csv=rmvpe,
        torchcrepe_csv=torchcrepe,
        fcpe_csv=fcpe,
        pesto_csv=pesto,
        config=FusionConfig(em_iterations=2),
    )
    assert out["backend"] == "adaptive_fusion"
    assert out["num_frames"] == 20
    assert out["frames"][10]["voiced"] is True
    assert abs(out["frames"][10]["f0_hz"] - a4) < 5.0
