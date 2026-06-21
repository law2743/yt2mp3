"""Run Step 1 analysis/transpose tests one file at a time."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = (
    "tests/unit/test_api.py",
    "tests/unit/test_auth.py",
    "tests/unit/test_config.py",
    "tests/unit/test_files.py",
    "tests/unit/test_job_manager.py",
    "tests/unit/test_key_analyzer.py",
    "tests/unit/test_key_mapping.py",
    "tests/unit/test_process.py",
    "tests/unit/test_url_validation.py",
    "tests/unit/test_ytdlp_errors.py",
    "tests/integration/test_pipeline.py",
    "tests/integration/test_transpose_audio.py",
)


def main() -> int:
    results: list[tuple[str, int]] = []
    print("\n=== Step 1：分析與轉調測試 ===", flush=True)
    for relative_path in TEST_FILES:
        print(f"\n--- {relative_path} ---", flush=True)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", relative_path, *sys.argv[1:]],
            cwd=ROOT,
            check=False,
        )
        results.append((relative_path, completed.returncode))

    print("\n=== Step 1 測試摘要 ===")
    for path, returncode in results:
        print(f"{'PASS' if returncode == 0 else 'FAIL'}  {path}")
    failures = sum(returncode != 0 for _, returncode in results)
    print(f"總計：{len(results)} files，{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
