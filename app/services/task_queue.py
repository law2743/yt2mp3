from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from app.models.melody import MeterHint


@dataclass(frozen=True, slots=True)
class QueueItem:
    job_id: str
    operation: Literal["analyze", "transpose", "melody"]
    semitones: int | None = None
    bitrate_kbps: int | None = None
    meter_hint: MeterHint = "auto"


class TaskQueue:
    """Small queue boundary that can later be replaced by separate CPU/GPU queues."""

    def __init__(self, maxsize: int):
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=maxsize)

    def full(self) -> bool:
        return self._queue.full()

    async def put(self, item: QueueItem) -> None:
        await self._queue.put(item)

    async def get(self) -> QueueItem:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()
