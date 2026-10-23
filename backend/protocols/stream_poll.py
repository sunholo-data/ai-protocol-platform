"""Poll a stream without moving its generator between asyncio contexts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import TypeVar

T = TypeVar("T")
IDLE = object()


async def poll_stream(source: AsyncIterator[T], interval_s: float | None) -> AsyncIterator[T | object]:
    """Yield source items or IDLE, keeping iteration and cleanup in one task.

    A fresh task for each ``anext`` breaks generators that reset ContextVar
    tokens in their finally block (including AG-UI's session read cache).
    A bounded queue provides backpressure while the caller sends heartbeats.
    """
    queue: asyncio.Queue[tuple[bool, T | BaseException | None]] = asyncio.Queue(maxsize=1)

    async def produce() -> None:
        it = source.__aiter__()
        try:
            try:
                async for item in it:
                    await queue.put((True, item))
            finally:
                aclose = getattr(it, "aclose", None)
                if aclose is not None:
                    await aclose()
        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                raise
            # Cancellation raised by the source itself must reach the caller;
            # otherwise it would keep receiving heartbeats forever.
            await queue.put((False, exc))
        except BaseException as exc:
            if asyncio.current_task().cancelling():
                raise
            await queue.put((False, exc))
        else:
            await queue.put((False, None))

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                is_item, value = await asyncio.wait_for(queue.get(), interval_s)
            except TimeoutError:
                yield IDLE
                continue
            if is_item:
                yield value
            elif isinstance(value, BaseException):
                raise value
            else:
                return
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
