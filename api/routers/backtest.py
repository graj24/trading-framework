from __future__ import annotations
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbols: Optional[list[str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    gap_threshold: float = 2.0
    sl_pct: float = 1.5
    target_pct: float = 3.0
    trail_pct: float = 0.5


def _stream(gen):
    for item in gen:
        yield json.dumps(item) + "\n"


@router.post("/gap")
def backtest_gap(req: BacktestRequest):
    from core.config import get_config
    cfg = get_config()
    symbols = req.symbols or cfg.get("watchlist", [])[:20]

    def generate():
        try:
            import sys
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent))
            from scripts.backtest_gap import run_backtest
            results = run_backtest(
                symbols=symbols,
                gap_threshold=req.gap_threshold,
                sl_pct=req.sl_pct,
                target_pct=req.target_pct,
            )
            total = len(results) if results else 0
            for i, r in enumerate(results or []):
                yield {"type": "trade", **r}
                if i % 5 == 0:
                    yield {"type": "progress", "pct": int((i + 1) / max(total, 1) * 100)}
            # summary
            if results:
                wins = [r for r in results if r.get("pnl_inr", 0) > 0]
                net = sum(r.get("pnl_inr", 0) for r in results)
                yield {
                    "type": "summary",
                    "trades": len(results),
                    "win_rate": round(len(wins) / len(results) * 100, 1),
                    "net_pnl": round(net, 2),
                }
            else:
                yield {"type": "summary", "trades": 0, "win_rate": 0, "net_pnl": 0}
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    return StreamingResponse(_stream(generate()), media_type="application/x-ndjson")


@router.post("/intraday")
def backtest_intraday(req: BacktestRequest):
    from core.config import get_config
    cfg = get_config()
    symbols = req.symbols or cfg.get("watchlist", [])[:20]

    def generate():
        try:
            from scripts.backtest_intraday import run_backtest as run_intraday
            results = run_intraday(symbols=symbols)
            total = len(results) if results else 0
            for i, r in enumerate(results or []):
                yield {"type": "trade", **r}
                if i % 5 == 0:
                    yield {"type": "progress", "pct": int((i + 1) / max(total, 1) * 100)}
            if results:
                wins = [r for r in results if r.get("pnl_inr", 0) > 0]
                net = sum(r.get("pnl_inr", 0) for r in results)
                yield {
                    "type": "summary",
                    "trades": len(results),
                    "win_rate": round(len(wins) / len(results) * 100, 1),
                    "net_pnl": round(net, 2),
                }
            else:
                yield {"type": "summary", "trades": 0, "win_rate": 0, "net_pnl": 0}
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    return StreamingResponse(_stream(generate()), media_type="application/x-ndjson")


@router.post("/replay")
def replay(req: BacktestRequest):
    def generate():
        try:
            from core.replay import run_replay
            from core.config import get_config
            cfg = get_config()
            symbols = req.symbols or cfg.get("watchlist", [])[:10]
            results = run_replay(
                symbols=symbols,
                start_date=req.start_date,
                end_date=req.end_date,
            )
            total = len(results) if results else 0
            for i, r in enumerate(results or []):
                yield {"type": "trade", **r}
                if i % 3 == 0:
                    yield {"type": "progress", "pct": int((i + 1) / max(total, 1) * 100)}
            if results:
                wins = [r for r in results if r.get("pnl_inr", 0) > 0]
                net = sum(r.get("pnl_inr", 0) for r in results)
                yield {
                    "type": "summary",
                    "trades": len(results),
                    "win_rate": round(len(wins) / len(results) * 100, 1),
                    "net_pnl": round(net, 2),
                }
            else:
                yield {"type": "summary", "trades": 0, "win_rate": 0, "net_pnl": 0}
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    return StreamingResponse(_stream(generate()), media_type="application/x-ndjson")
