from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.models.vocal_pitch import VocalPitchResult
from app.services.gpu_subprocess_env import build_gpu_subprocess_env


def _summary(stderr: bytes, stdout: bytes) -> str:
    text = (stderr or stdout).decode("utf-8", errors="replace").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "RMVPE exited without an error message")[:500]


class RmvpePitchBackend:
    backend_name = "rmvpe_onnx"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _environment(self) -> dict[str, str]:
        return build_gpu_subprocess_env(mode="onnx", gpu_python=self.settings.rmvpe_python)

    async def _run(
        self, *command: str, timeout_seconds: int | None = None
    ) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._environment(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds or self.settings.rmvpe_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        return process.returncode or 0, stdout, stderr

    async def extract(self, input_wav: Path, output_json: Path) -> VocalPitchResult:
        if not self.settings.rmvpe_python.is_file():
            raise RuntimeError("The configured RMVPE Python executable is unavailable.")
        script = Path(__file__).resolve().parents[3] / "scripts" / "run_rmvpe_pitch.py"
        if not script.is_file():
            raise RuntimeError("RMVPE pitch extraction script is missing.")
        output_json.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_json.with_name(".vocal_pitch.json.tmp")
        temporary.unlink(missing_ok=True)
        output_json.unlink(missing_ok=True)
        command = (
            str(self.settings.rmvpe_python),
            str(script),
            str(input_wav),
            str(temporary),
            "--voiced-confidence-threshold",
            str(self.settings.rmvpe_voiced_confidence_threshold),
        )
        try:
            returncode, stdout, stderr = await self._run(*command)
        except TimeoutError as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("RMVPE pitch extraction timed out.") from exc
        if returncode != 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"RMVPE pitch extraction failed: {_summary(stderr, stdout)}")
        try:
            result = VocalPitchResult.model_validate_json(temporary.read_text(encoding="utf-8"))
            normalized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
            temporary.write_text(normalized + "\n", encoding="utf-8")
            output_json.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(output_json)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            output_json.unlink(missing_ok=True)
            raise RuntimeError("RMVPE pitch extraction produced invalid output.") from exc
        return result
