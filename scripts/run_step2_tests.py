"""Run Step 2 melody tests one file at a time."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = (
    "tests/unit/test_melody.py",
    "tests/unit/test_melody_api.py",
    "tests/integration/test_melody_audio.py",
)


def main() -> int:
    results: list[tuple[str, int]] = []
    print("\n=== Step 2：主旋律簡譜測試 ===", flush=True)
    for relative_path in TEST_FILES:
        print(f"\n--- {relative_path} ---", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", relative_path, *sys.argv[1:]],
            cwd=ROOT,
            check=False,
        )
        results.append((relative_path, completed.returncode))

    print("\n=== Step 2 測試摘要 ===")
    for path, returncode in results:
        print(f"{'PASS' if returncode == 0 else 'FAIL'}  {path}")
    failures = sum(returncode != 0 for _, returncode in results)
    print(f"總計：{len(results)} files，{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
