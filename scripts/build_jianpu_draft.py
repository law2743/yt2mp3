from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from app.services.artifacts import JobArtifacts
    from app.services.rhythm.numbered_notation import (
        build_numbered_notation,
        write_jianpu_draft_txt,
        write_numbered_notation_json,
    )

    parser = argparse.ArgumentParser(description="Build jianpu draft artifacts from notes_draft.json.")
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--key")
    parser.add_argument("--mode")
    args = parser.parse_args()

    artifacts = JobArtifacts(args.job_dir)
    notes_draft_path = artifacts.rhythm_notes_draft_json
    if not notes_draft_path.exists():
        print(f"Missing notes_draft.json: {notes_draft_path}", file=sys.stderr)
        return 1

    try:
        result = build_numbered_notation(notes_draft_path, key=args.key, mode=args.mode)
        write_numbered_notation_json(result, artifacts.rhythm_numbered_notation_json)
        write_jianpu_draft_txt(result, artifacts.rhythm_jianpu_draft_txt)
    except Exception as exc:
        print(f"Failed to build jianpu draft: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {artifacts.rhythm_numbered_notation_json}")
    print(f"Wrote {artifacts.rhythm_jianpu_draft_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
