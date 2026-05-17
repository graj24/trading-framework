from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)

# Connected clients
_clients: Set[WebSocket] = set()


async def broadcast(event: dict):
    """Send an event to all connected WebSocket clients."""
    dead = set()
    for ws in _clients:
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def _mock_feed():
    """Push mock market data every 2s when no real data is available."""
    import random
    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
    base_prices = {"RELIANCE": 2847, "TCS": 3920, "HDFCBANK": 1680, "INFY": 1540, "ICICIBANK": 1240}

    while True:
        await asyncio.sleep(2)
        if not _clients:
            continue
        sym = random.choice(symbols)
        base = base_prices[sym]
        price = round(base + random.uniform(-10, 10), 2)
        change_pct = round((price - base) / base * 100, 3)
        base_prices[sym] = price

        await broadcast({"type": "ltp_update", "symbol": sym, "price": price, "change_pct": change_pct})

        # Occasionally send pnl update
        if random.random() < 0.3:
            await broadcast({
                "type": "pnl_update",
                "total_pnl_inr": round(random.uniform(-500, 2000), 2),
                "total_pnl_pct": round(random.uniform(-0.5, 2.0), 2),
            })


_feed_task: asyncio.Task | None = None


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    global _feed_task
    await ws.accept()
    _clients.add(ws)
    logger.info("WS client connected. Total: %d", len(_clients))

    # Start mock feed if not running
    if _feed_task is None or _feed_task.done():
        _feed_task = asyncio.create_task(_mock_feed())

    try:
        # Send initial state
        await ws.send_text(json.dumps({
            "type": "connected",
            "timestamp": datetime.now().isoformat(),
            "message": "Bloomberg Terminal live feed connected",
        }))
        # Keep alive — listen for client messages (ping/pong)
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)
        logger.info("WS client disconnected. Total: %d", len(_clients))
