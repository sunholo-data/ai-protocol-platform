"""Stream polling owns the generator Context and awaits disconnect cleanup."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

import pytest
from ag_ui.core import RunStartedEvent, StateDeltaEvent

from adk.agui import stream_agui_events
from protocols.stream_poll import IDLE, poll_stream


async def test_iteration_and_cleanup_share_one_context():
    owner = ContextVar("owner", default="caller")
    closed = asyncio.Event()

    async def source():
        token = owner.set("stream")
        try:
            for _ in range(3):
                await asyncio.sleep(0)
                assert owner.get() == "stream"
                yield "chunk"
        finally:
            owner.reset(token)
            closed.set()

    assert [item async for item in poll_stream(source(), None)] == ["chunk"] * 3
    assert closed.is_set()
    assert owner.get() == "caller"


@pytest.mark.parametrize("bridge", [False, True])
async def test_disconnect_awaits_source_cleanup_in_its_context(bridge):
    owner = ContextVar("owner", default="caller")
    closed = asyncio.Event()

    async def source():
        token = owner.set("stream")
        try:
            yield RunStartedEvent(thread_id="t", run_id="r")
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            owner.reset(token)
            closed.set()

    class Agent:
        def run(self, _):
            return source()

    stream = stream_agui_events(Agent(), None, heartbeat_seconds=0) if bridge else poll_stream(source(), None)
    await anext(stream)
    await stream.aclose()
    assert closed.is_set()
    assert owner.get() == "caller"


class _Interrupt(BaseException):
    pass


@pytest.mark.parametrize("signal", [RuntimeError("source failed"), asyncio.CancelledError(), _Interrupt("pause")])
async def test_source_control_flow_and_errors_reach_the_caller(signal):
    closed = asyncio.Event()

    async def source():
        try:
            yield "chunk"
            raise signal
        finally:
            closed.set()

    async def collect():
        return [item async for item in poll_stream(source(), 0.01)]

    with pytest.raises(type(signal)) as caught:
        await asyncio.wait_for(collect(), timeout=2)
    assert caught.value is signal
    assert closed.is_set()


async def test_cancelling_a_waiting_consumer_awaits_cleanup():
    waiting = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            yield "chunk"
            waiting.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    stream = poll_stream(source(), 0.01)
    assert await anext(stream) == "chunk"
    consumer = asyncio.create_task(anext(stream))
    await waiting.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert closed.is_set()


async def test_idle_polling_does_not_cancel_the_source():
    ready = asyncio.Event()

    async def source():
        await ready.wait()
        yield "chunk"

    stream = poll_stream(source(), 0.001)
    assert await anext(stream) is IDLE
    ready.set()
    assert await anext(stream) == "chunk"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_typed_state_delta_keeps_null_patch_values():
    class Agent:
        async def run(self, _):
            yield RunStartedEvent(thread_id="t", run_id="r")
            yield StateDeltaEvent(delta=[{"op": "replace", "path": "/selection", "value": None}])

    events = [event async for event in stream_agui_events(Agent(), None, heartbeat_seconds=0)]
    patch = next(event for event in events if event["type"] == "STATE_DELTA")
    assert patch["delta"] == [{"op": "replace", "path": "/selection", "value": None}]
