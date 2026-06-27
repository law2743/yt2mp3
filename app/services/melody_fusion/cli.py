from __future__ import annotations

import argparse
from pathlib import Path

from .fusion import FusionConfig, fuse_pitch_csvs


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.melody_fusion.cli",
        description="Fuse RMVPE/torchcrepe/FCPE/PESTO pitch CSVs into adaptive_fusion CSV/JSON.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    fuse = sub.add_parser("fuse", help="run adaptive melody fusion")
    fuse.add_argument("--rmvpe", "--rmvpe-csv", type=Path, default=None)
    fuse.add_argument("--torchcrepe", "--torchcrepe-csv", type=Path, default=None)
    fuse.add_argument("--fcpe", "--fcpe-csv", type=Path, default=None)
    fuse.add_argument("--pesto", "--pesto-csv", type=Path, default=None)
    fuse.add_argument("--out", type=Path, required=True, help="output fusion.json path")
    fuse.add_argument("--csv-out", type=Path, default=None, help="optional output fusion.csv path")
    fuse.add_argument("--diagnostics-out", type=Path, default=None, help="optional diagnostics.json path")
    fuse.add_argument("--timeline", default="intersection", help="intersection, union, or reference:<backend>")
    fuse.add_argument("--reference-backend", default=None, help="explicit backend timeline to copy, e.g. rmvpe")
    fuse.add_argument("--em-iterations", type=int, default=2)
    fuse.add_argument("--cluster-cents", type=float, default=50.0)
    fuse.add_argument("--agreement-cents", type=float, default=50.0)
    fuse.add_argument("--min-voiced-ms", type=float, default=60.0)
    fuse.add_argument("--voiced-threshold", type=float, default=0.35)
    args = parser.parse_args()

    if args.cmd == "fuse":
        cfg = FusionConfig(
            timeline_mode=args.timeline,
            reference_backend=args.reference_backend,
            em_iterations=args.em_iterations,
            cluster_cents=args.cluster_cents,
            agreement_cents=args.agreement_cents,
            min_voiced_ms=args.min_voiced_ms,
            voiced_threshold=args.voiced_threshold,
        )
        payload = fuse_pitch_csvs(
            rmvpe_csv=args.rmvpe,
            torchcrepe_csv=args.torchcrepe,
            fcpe_csv=args.fcpe,
            pesto_csv=args.pesto,
            output_json_path=args.out,
            output_csv_path=args.csv_out,
            diagnostics_path=args.diagnostics_out,
            config=cfg,
        )
        fusion = payload.get("fusion", {})
        print(f"wrote {args.out}")
        print(f"frames={payload.get('num_frames')} voiced_ratio={payload.get('voiced_ratio')}")
        if fusion.get("warnings"):
            print("warnings:")
            for warning in fusion["warnings"]:
                print(f"- {warning}")


if __name__ == "__main__":
    main()
