from __future__ import annotations

import asyncio
import codecs
import os
import re
import signal
from collections.abc import Callable
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
    stdout_line_callback: Callable[[str], None] | None = None,
    stderr_line_callback: Callable[[str], None] | None = None,
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

    async def read_stream(
        stream: asyncio.StreamReader,
        callback: Callable[[str], None] | None,
    ) -> bytes:
        chunks: list[bytes] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while chunk := await stream.read(1024):
            chunks.append(chunk)
            if callback:
                parts = re.split(r"[\r\n]+", pending + decoder.decode(chunk))
                pending = parts.pop()
                for part in parts:
                    if part:
                        callback(part)
        if callback:
            pending += decoder.decode(b"", final=True)
            if pending:
                callback(pending)
        return b"".join(chunks)

    async def communicate() -> tuple[bytes, bytes]:
        assert process.stdout and process.stderr
        stdout_bytes, stderr_bytes = await asyncio.gather(
            read_stream(process.stdout, stdout_line_callback),
            read_stream(process.stderr, stderr_line_callback),
        )
        # Avoid a subprocess transport race observed on Python 3.12 where the
        # child has exited and both pipes reached EOF, but process.wait() never
        # wakes its waiter. The return code is still updated by the transport.
        while process.returncode is None:
            await asyncio.sleep(0.001)
        return stdout_bytes, stderr_bytes

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(communicate(), timeout)
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
