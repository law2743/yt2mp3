from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path


class ProcessFailed(RuntimeError):
    def __init__(self, command: str, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr[-8000:]
        super().__init__(f"{command} exited with status {returncode}")


class ProcessTimedOut(RuntimeError):
    pass


@dataclass(slots=True)
class ProcessResult:
    stdout: str
    stderr: str


async def run_process(
    args: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
) -> ProcessResult:
    if not args:
        raise ValueError("command cannot be empty")
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError as exc:
        await _terminate_group(process)
        raise ProcessTimedOut(f"{Path(args[0]).name} exceeded {timeout:.0f}s timeout") from exc
    except asyncio.CancelledError:
        await _terminate_group(process)
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if process.returncode:
        raise ProcessFailed(Path(args[0]).name, process.returncode, stderr)
    return ProcessResult(stdout, stderr)


async def _terminate_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

