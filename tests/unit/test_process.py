import sys

import pytest

from app.services.process import ProcessFailed, ProcessTimedOut, run_process


@pytest.mark.asyncio
async def test_process_success():
    result = await run_process([sys.executable, "-c", "print('ok')"], timeout=2)
    assert result.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_process_streams_stdout_lines():
    lines = []
    await run_process(
        [sys.executable, "-c", "print('25%'); print('100%')"],
        timeout=5,
        stdout_line_callback=lines.append,
    )
    assert lines == ["25%", "100%"]


@pytest.mark.asyncio
async def test_process_streams_carriage_return_progress():
    lines = []
    await run_process(
        [sys.executable, "-c", "import sys; sys.stderr.write('1%\\r50%\\r100%\\n')"],
        timeout=5,
        stderr_line_callback=lines.append,
    )
    assert lines == ["1%", "50%", "100%"]


@pytest.mark.asyncio
async def test_process_failure_hides_full_command():
    with pytest.raises(ProcessFailed) as error:
        await run_process([sys.executable, "-c", "raise SystemExit(3)"], timeout=2)
    assert error.value.returncode == 3


@pytest.mark.asyncio
async def test_process_timeout_terminates_process_group():
    with pytest.raises(ProcessTimedOut):
        await run_process([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
