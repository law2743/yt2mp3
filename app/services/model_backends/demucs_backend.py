from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from app.config import Settings
from app.models.stem import StemSeparationMetadata
from app.services.stem_separator import StemSeparationRequest


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _summary(stderr: bytes, stdout: bytes) -> str:
    text = (stderr or stdout).decode("utf-8", errors="replace").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "Demucs exited without an error message")[:500]


class DemucsStemSeparator:
    backend_name = "demucs"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self.settings.demucs_clean_env:
            environment.pop("LD_LIBRARY_PATH", None)
            environment.pop("PYTHONPATH", None)
        return environment

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
                timeout=timeout_seconds or self.settings.demucs_timeout_seconds,
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

    async def probe_device(self) -> tuple[str | None, str | None]:
        python = self.settings.demucs_python
        if not python.is_file() or not os.access(python, os.X_OK):
            return None, "The configured Demucs Python executable is unavailable."
        code = (
            "import importlib.util, torch; "
            "assert importlib.util.find_spec('demucs') is not None; "
            "print('cuda' if torch.cuda.is_available() else 'cpu')"
        )
        try:
            returncode, stdout, stderr = await self._run(
                str(python), "-c", code, timeout_seconds=30
            )
        except TimeoutError:
            return None, "Demucs environment probe timed out."
        except OSError as exc:
            return None, f"Demucs environment probe failed: {exc}"
        if returncode != 0:
            return None, f"Demucs environment is unavailable: {_summary(stderr, stdout)}"
        detected = stdout.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        return (detected[0] if detected and detected[0] in {"cuda", "cpu"} else None), None

    async def separate(self, request: StemSeparationRequest) -> StemSeparationMetadata:
        requested_device = self.settings.stem_separation_device
        detected_device, probe_error = await self.probe_device()
        if probe_error:
            raise RuntimeError(probe_error)
        device = detected_device if requested_device == "auto" else requested_device
        if device == "cuda" and detected_device != "cuda":
            raise RuntimeError("CUDA is unavailable in the configured Demucs environment.")
        if device == "cpu" and not self.settings.allow_cpu_heavy_mode:
            raise RuntimeError("CPU Demucs is disabled by ALLOW_CPU_HEAVY_MODE=false.")

        request.stems_dir.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=".demucs-", dir=request.stems_dir))
        try:
            command = (
                str(self.settings.demucs_python),
                "-m",
                "demucs",
                f"--two-stems={self.settings.demucs_two_stems}",
                "-n",
                self.settings.demucs_model,
                "-d",
                device,
                "-o",
                str(temporary_root),
                str(request.source_audio),
            )
            try:
                returncode, stdout, stderr = await self._run(*command)
            except TimeoutError as exc:
                raise RuntimeError("Demucs separation timed out.") from exc
            if returncode != 0:
                raise RuntimeError(f"Demucs separation failed: {_summary(stderr, stdout)}")

            vocals = list(temporary_root.glob("*/*/vocals.wav"))
            accompaniment = list(temporary_root.glob("*/*/no_vocals.wav"))
            if len(vocals) != 1 or len(accompaniment) != 1:
                raise RuntimeError(
                    "Demucs did not produce one vocals.wav and no_vocals.wav output."
                )
            if vocals[0].stat().st_size <= 44 or accompaniment[0].stat().st_size <= 44:
                raise RuntimeError("Demucs produced an empty stem artifact.")

            temporary_vocals = request.stems_dir / ".vocals.wav.tmp"
            temporary_accompaniment = request.stems_dir / ".accompaniment.wav.tmp"
            shutil.copyfile(vocals[0], temporary_vocals)
            shutil.copyfile(accompaniment[0], temporary_accompaniment)
            temporary_vocals.replace(request.vocals_output)
            temporary_accompaniment.replace(request.accompaniment_output)
            return StemSeparationMetadata(
                status="completed",
                backend="demucs",
                model=self.settings.demucs_model,
                device=device,
                source_path=_relative(request.source_audio, request.job_root),
                vocals_path=_relative(request.vocals_output, request.job_root),
                accompaniment_path=_relative(request.accompaniment_output, request.job_root),
            )
        finally:
            (request.stems_dir / ".vocals.wav.tmp").unlink(missing_ok=True)
            (request.stems_dir / ".accompaniment.wav.tmp").unlink(missing_ok=True)
            shutil.rmtree(temporary_root, ignore_errors=True)
