"""In-process pub/sub for live dashboard events.

K2 ships a single-process control plane; the activity stream lives in
memory of the FastAPI process. K3+ may swap this for NATS or Postgres
LISTEN/NOTIFY when we need cross-process distribution. The shape of
``Event`` is the wire shape: type + ts + payload, nothing more, so the
swap is internal.

Backpressure rule: subscriber queues are bounded; on full, the publish
drops the event for that subscriber. We choose drop-on-slow-consumer
over back-pressuring the publisher because the publisher is on the hot
path (a heartbeat tick, a workflow signal handler) and a stuck dashboard
must not stall the control plane. Subscribers resync on reconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from loguru import logger

EventType = Literal["agent.lifecycle", "mode.changed", "pm.heartbeat"]


@dataclass(slots=True, frozen=True)
class Event:
    """One bus event. ``ts`` is ISO8601 UTC; ``payload`` is JSON-safe."""

    type: str
    ts: str
    payload: dict[str, Any]


class EventBus:
    """Process-local async pub/sub.

    ``publish`` never awaits per-subscriber delivery — it iterates the
    subscriber set with ``put_nowait`` so a single slow consumer can't
    stall the others. ``subscribe`` is an async generator that holds a
    bounded queue for the lifetime of the iteration; the queue is
    discarded on cleanup so we don't leak across reconnects.
    """

    def __init__(self, *, max_queue_size: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._max_queue_size = max_queue_size

    @property
    def subscriber_count(self) -> int:
        """For tests and /api/health debugging."""
        return len(self._subscribers)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast one event to every current subscriber. Never blocks.

        Accepts ``str`` for ``event_type`` so worker-side publishers
        (which talk JSON over HTTP) don't have to import the literal.
        Validation is the dashboard's problem.
        """
        event = Event(
            type=event_type,
            ts=datetime.now(UTC).isoformat(),
            payload=payload,
        )
        # Snapshot the subscriber set so a concurrent unsubscribe (e.g.
        # a websocket client closing during the broadcast) doesn't
        # mutate the iterator we're walking.
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer; drop. They'll resync on reconnect.
                logger.debug(
                    "event_bus: dropped {} for slow subscriber (queue full)",
                    event_type,
                )

    async def subscribe(self) -> AsyncIterator[Event]:
        """Async iterator over events delivered after this call.

        Late subscribers don't see history — the bus is fire-and-forget.
        Tests and the WS handler should treat the iterator as
        single-use; the queue is discarded when the iteration ends.
        """
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._subscribers.discard(q)


__all__ = ["Event", "EventBus", "EventType"]
