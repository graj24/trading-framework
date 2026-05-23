"""Pure unit tests for the in-process event bus.

The bus is the load-bearing piece of K2 Step 2.5. Three properties matter
operationally and are tested here:

  1. Multiple subscribers all see a published event.
  2. A slow consumer (full queue) does NOT block the publisher — that's
     the contract that lets the API stay responsive when the dashboard
     stalls.
  3. The async generator ``subscribe()`` cleans up the subscriber queue
     on iteration teardown, so reconnects don't leak.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from agora.platform.control_plane.event_bus import EventBus


async def _drain_one(bus: EventBus) -> tuple[asyncio.Task[None], asyncio.Queue[dict[str, Any]]]:
    """Spawn a subscriber task that reads exactly one event into a queue.

    Returns the task and the result queue. Caller awaits the queue's
    ``get()`` to consume; cancels the task to clean up.
    """
    received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _reader() -> None:
        async for event in bus.subscribe():
            await received.put({"type": event.type, "payload": event.payload})
            return

    task = asyncio.create_task(_reader())
    # Yield once so the subscriber registers before we publish.
    await asyncio.sleep(0)
    return task, received


async def test_publish_delivers_to_all_subscribers() -> None:
    bus = EventBus()
    task_a, q_a = await _drain_one(bus)
    task_b, q_b = await _drain_one(bus)
    # Both subscribers must be registered before publish or they'll
    # miss the event (no replay).
    assert bus.subscriber_count == 2

    await bus.publish("agent.lifecycle", {"agent_id": "pm1", "event": "started"})

    got_a = await asyncio.wait_for(q_a.get(), timeout=1.0)
    got_b = await asyncio.wait_for(q_b.get(), timeout=1.0)
    assert got_a == {
        "type": "agent.lifecycle",
        "payload": {"agent_id": "pm1", "event": "started"},
    }
    assert got_b == got_a

    # Drain the (empty) iteration to free the queue. We never put a
    # second event, so the readers exit naturally after their one read.
    await asyncio.gather(task_a, task_b)


async def test_subscriber_disconnect_doesnt_block_publish() -> None:
    """A full subscriber queue must not back-pressure publish().

    We construct a bus with max_queue_size=1, register a subscriber but
    never read from it, then push two events. Both publishes must
    return promptly (not block on a put). The second event is dropped
    for the slow subscriber — that's the documented behavior.
    """
    bus = EventBus(max_queue_size=1)

    # Easier path: start a task that registers via subscribe() and parks.
    async def _registrar() -> None:
        async for _ in bus.subscribe():
            await asyncio.sleep(10)  # never resumes — cancelled below

    parked = asyncio.create_task(_registrar())
    # Wait for the parked subscriber to register its queue.
    for _ in range(50):
        if bus.subscriber_count == 1:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("subscriber never registered")

    # Two publishes; second must be dropped silently for the slow
    # consumer. Both calls must return well under any blocking timeout.
    await asyncio.wait_for(bus.publish("pm.heartbeat", {"i": 1}), timeout=0.5)
    await asyncio.wait_for(bus.publish("pm.heartbeat", {"i": 2}), timeout=0.5)

    parked.cancel()
    with suppress(asyncio.CancelledError):
        await parked


async def test_unsubscribe_after_iteration_breaks() -> None:
    """When a ``subscribe()`` async-generator is closed, its queue is removed.

    The production caller (``WS /api/stream``) hits cleanup either via
    ``WebSocketDisconnect`` propagating through the ``async for``, or
    via the surrounding task being cancelled. Both run the generator's
    ``finally`` clause. We exercise the cancellation path here because
    it's the only deterministic way to drive the cleanup in a test
    (returning from the loop body leaves the generator paused until GC).
    """
    bus = EventBus()

    async def _drive() -> None:
        async for _ in bus.subscribe():
            pass  # never reached — we cancel before publishing

    task = asyncio.create_task(_drive())
    # Wait for the subscriber to register its queue.
    for _ in range(50):
        if bus.subscriber_count == 1:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("subscriber never registered")

    # Cancel the task; the generator's ``finally`` runs (via the task's
    # CancelledError propagation) and discards the queue from the bus.
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    # Yield to the event loop so the generator's finally has a chance
    # to run on the same tick — covers a residual scheduling race.
    await asyncio.sleep(0)

    assert bus.subscriber_count == 0
