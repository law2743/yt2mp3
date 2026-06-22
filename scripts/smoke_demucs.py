#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    environment.pop("PYTHONPATH", None)
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local Demucs GPU smoke test.")
    parser.add_argument("audio", type=Path)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path("/home/startech/venvs/yt2mp3-gpu/bin/python"),
    )
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    if not args.audio.is_file():
        parser.error(f"audio file does not exist: {args.audio}")
    if not args.python.is_file():
        parser.error(f"Demucs Python does not exist: {args.python}")

    environment = clean_environment()
    probe = subprocess.run(
        [
            str(args.python),
            "-c",
            "import torch; print(torch.__version__, torch.cuda.is_available(), "
            "torch.version.cuda, torch.cuda.get_device_name(0) if torch.cuda.is_available() "
            "else None)",
        ],
        check=False,
        env=environment,
    )
    if probe.returncode:
        return probe.returncode

    with tempfile.TemporaryDirectory(prefix="yt2mp3-demucs-smoke-") as directory:
        command = [
            str(args.python),
            "-m",
            "demucs",
            "--two-stems=vocals",
            "-n",
            args.model,
            "-d",
            args.device,
            "-o",
            directory,
            str(args.audio.resolve()),
        ]
        completed = subprocess.run(command, check=False, env=environment)
        if completed.returncode:
            return completed.returncode
        output = Path(directory)
        vocals = list(output.glob("*/*/vocals.wav"))
        accompaniment = list(output.glob("*/*/no_vocals.wav"))
        if len(vocals) != 1 or len(accompaniment) != 1:
            print("Smoke test failed: expected vocals.wav and no_vocals.wav.")
            return 1
        print(f"Smoke test passed: {vocals[0]}")
        print(f"Smoke test passed: {accompaniment[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
