from __future__ import annotations
from fastapi import APIRouter, Query
import yfinance as yf

router = APIRouter(prefix="/api/candles", tags=["candles"])

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1d": "1d",
}
PERIOD_MAP = {
    "1m": "1d", "5m": "5d", "15m": "5d", "30m": "1mo",
    "1h": "3mo", "1d": "1y",
}


@router.get("/{symbol}")
def get_candles(
    symbol: str,
    interval: str = Query("1d", regex="^(1m|5m|15m|30m|1h|1d)$"),
    period: str = Query(None),
):
    iv = INTERVAL_MAP.get(interval, "1d")
    per = period or PERIOD_MAP.get(interval, "1y")
    try:
        t = yf.Ticker(symbol.upper() + ".NS")
        hist = t.history(period=per, interval=iv)
        if hist.empty:
            return []
        candles = []
        for ts, row in hist.iterrows():
            candles.append({
                "time": int(ts.timestamp()),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        return candles
    except Exception as e:
        return {"error": str(e)}
