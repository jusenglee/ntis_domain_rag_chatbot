from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Optional

from apps.api.streaming.contracts import StreamEvent


class AsyncStreamEmitter:
    def __init__(self, *, maxsize: int = 128) -> None:
        self._queue: asyncio.Queue[Optional[StreamEvent]] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self._seq = 0

    async def publish(self, event: StreamEvent) -> None:
        if self._closed:
            return
        self._seq += 1
        await self._queue.put(replace(event, seq=self._seq))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)

    async def next_event(self) -> Optional[StreamEvent]:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def closed(self) -> bool:
        return self._closed
