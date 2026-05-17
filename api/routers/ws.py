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


def _nse_price(session, sym: str) -> tuple[float, float] | None:
    """Fetch price + prev_close from NSE. Returns (price, prev_close) or None."""
    try:
        r = session.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={sym}",
            timeout=5
        )
        if r.status_code != 200:
            return None
        pi = r.json().get("priceInfo", {})
        price = pi.get("lastPrice") or pi.get("close")
        prev = pi.get("previousClose") or pi.get("close")
        if not price:
            return None
        return float(price), float(prev or price)
    except Exception:
        return None


def _make_nse_session():
    import requests as _req
    s = _req.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com",
    })
    try:
        s.get("https://www.nseindia.com", timeout=5)
    except Exception:
        pass
    return s


async def _real_feed():
    """Push real NSE prices for the full watchlist every 60s."""
    from core.config import get_config

    session = _make_nse_session()
    loop = asyncio.get_event_loop()

    while True:
        await asyncio.sleep(60)
        if not _clients:
            continue
        try:
            config = get_config()
            symbols = config.get("watchlist", [])[:50]

            async def _fetch_and_broadcast(sym):
                result = await loop.run_in_executor(None, _nse_price, session, sym)
                if result:
                    price, prev = result
                    change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                    await broadcast({"type": "ltp_update", "symbol": sym,
                                     "price": price, "change_pct": change_pct})

            await asyncio.gather(*[_fetch_and_broadcast(s) for s in symbols])
        except Exception as e:
            logger.warning(f"Real feed error: {e}")
            session = _make_nse_session()


_feed_task: asyncio.Task | None = None


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    global _feed_task
    await ws.accept()
    _clients.add(ws)
    logger.info("WS client connected. Total: %d", len(_clients))

    # Start real feed if not running
    if _feed_task is None or _feed_task.done():
        _feed_task = asyncio.create_task(_real_feed())

    try:
        # Send initial snapshot immediately on connect
        try:
            from core.config import get_config
            config = get_config()
            symbols = config.get("watchlist", [])[:50]
            snap_session = _make_nse_session()
            loop = asyncio.get_event_loop()

            async def _snap(sym):
                result = await loop.run_in_executor(None, _nse_price, snap_session, sym)
                if result:
                    price, prev = result
                    change_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                    await ws.send_text(json.dumps({
                        "type": "ltp_update", "symbol": sym,
                        "price": price, "change_pct": change_pct,
                    }))

            await asyncio.gather(*[_snap(s) for s in symbols])
        except Exception as e:
            logger.warning(f"Initial snapshot failed: {e}")

        await ws.send_text(json.dumps({
            "type": "connected",
            "timestamp": datetime.now().isoformat(),
            "message": "Bloomberg Terminal live feed connected",
        }))
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


@router.websocket("/ws/pm_events")
async def websocket_pm_events(ws: WebSocket, pm_id: str | None = None):
    """Stream events.db in real-time to the PM monitoring page. Optional ?pm_id= filter."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    await ws.accept()
    logger.info("PM events WS client connected (pm_id=%s)", pm_id)

    EVENTS_DB = _Path("events.db")
    cursor = 0

    # Send recent historical events on connect (last 50) so the page isn't empty
    if EVENTS_DB.exists():
        try:
            with _sqlite3.connect(EVENTS_DB) as conn:
                conn.row_factory = _sqlite3.Row
                if pm_id:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM events WHERE pm_id = ? ORDER BY id DESC LIMIT 50) ORDER BY id ASC",
                        (pm_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM (SELECT * FROM events ORDER BY id DESC LIMIT 50) ORDER BY id ASC"
                    ).fetchall()
            for row in rows:
                d = dict(row)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
                cursor = max(cursor, d["id"])
                await ws.send_text(json.dumps({"type": "pm_event", "event": d, "historical": True}))
        except Exception as e:
            logger.debug(f"Backfill error: {e}")

    await ws.send_text(json.dumps({"type": "cursor", "latest_id": cursor}))

    try:
        while True:
            # Poll for new events every 500ms
            await asyncio.sleep(0.5)
            if not EVENTS_DB.exists():
                continue
            try:
                with _sqlite3.connect(EVENTS_DB) as conn:
                    conn.row_factory = _sqlite3.Row
                    if pm_id:
                        rows = conn.execute(
                            "SELECT * FROM events WHERE id > ? AND pm_id = ? ORDER BY id LIMIT 50",
                            (cursor, pm_id),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 50",
                            (cursor,),
                        ).fetchall()
                for row in rows:
                    d = dict(row)
                    try:
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                    cursor = d["id"]
                    await ws.send_text(json.dumps({"type": "pm_event", "event": d}))
            except Exception as e:
                logger.debug("PM events WS poll error: %s", e)

            # Handle client pings
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=0.01)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif msg.get("type") == "seek":
                    # Client can seek to a specific event id for replay
                    cursor = int(msg.get("from_id", cursor))
                    await ws.send_text(json.dumps({"type": "seeked", "cursor": cursor}))
            except (asyncio.TimeoutError, Exception):
                pass

    except WebSocketDisconnect:
        pass
    finally:
        logger.info("PM events WS client disconnected")


@router.websocket("/ws/journal/{pm_id}")
async def websocket_journal(ws: WebSocket, pm_id: str):
    """Tail pm_<id>/state/journal.md in real-time."""
    from pathlib import Path as _Path
    await ws.accept()
    journal_path = _Path(f"pm_{pm_id}/state/journal.md")
    last_size = journal_path.stat().st_size if journal_path.exists() else 0

    try:
        # Send existing content first
        if journal_path.exists():
            await ws.send_text(json.dumps({
                "type": "journal_init",
                "pm_id": pm_id,
                "content": journal_path.read_text()[-8000:],  # last 8k chars
            }))
        while True:
            await asyncio.sleep(2)
            if not journal_path.exists():
                continue
            size = journal_path.stat().st_size
            if size > last_size:
                # Read only the new bytes
                with open(journal_path, "rb") as f:
                    f.seek(last_size)
                    new_content = f.read().decode("utf-8", errors="replace")
                last_size = size
                await ws.send_text(json.dumps({
                    "type": "journal_append",
                    "pm_id": pm_id,
                    "content": new_content,
                }))
            # Handle pings
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=0.01)
                if json.loads(data).get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except (asyncio.TimeoutError, Exception):
                pass
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/leaderboard")
async def websocket_leaderboard(ws: WebSocket):
    """Push leaderboard updates every 30s."""
    await ws.accept()
    try:
        while True:
            try:
                from common.leaderboard.snapshot import get_leaderboard
                board = get_leaderboard()
                await ws.send_text(json.dumps({"type": "leaderboard", "data": board}))
            except Exception as e:
                logger.debug(f"Leaderboard WS error: {e}")
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
