"""Background mode-watcher.

Polls the mode controller every 30 seconds and logs the current mode (plus any
transition). K1 only logs; K2 will publish to NATS so PMs and other workers
can react without polling. Nothing in K1 starts this loop — it's machinery
ready for K2 to wire up.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger

from agora.platform.control_plane import mode as mode_module

POLL_INTERVAL_S: float = 30.0


async def mode_loop(stop_event: asyncio.Event | None = None) -> None:
    """Run the poll-and-log loop until `stop_event` is set.

    Pass an asyncio.Event to allow graceful shutdown:
        stop = asyncio.Event()
        task = asyncio.create_task(mode_loop(stop))
        ...
        stop.set(); await task
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    last_mode: str | None = None
    logger.info("mode_loop starting (poll every {}s)", POLL_INTERVAL_S)
    try:
        while not stop_event.is_set():
            now = datetime.now(UTC)
            result = mode_module.compute_mode(now)
            if result.mode != last_mode:
                logger.info(
                    "mode={mode} as_of={ts} next={nt}",
                    mode=result.mode,
                    ts=now.isoformat(),
                    nt=result.next_transition,
                )
                last_mode = result.mode
            else:
                logger.debug("mode unchanged: {}", result.mode)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_S)
            except TimeoutError:
                continue
    finally:
        logger.info("mode_loop stopping")
