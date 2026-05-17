from __future__ import annotations
from fastapi import APIRouter
import yfinance as yf

router = APIRouter(prefix="/api/market", tags=["market"])

SECTOR_TICKERS = {
    "NIFTY50":    "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "IT":         "^CNXIT",
    "PHARMA":     "^CNXPHARMA",
    "AUTO":       "^CNXAUTO",
    "FMCG":       "^CNXFMCG",
    "METAL":      "^CNXMETAL",
    "REALTY":     "^CNXREALTY",
    "ENERGY":     "^CNXENERGY",
    "INFRA":      "^CNXINFRA",
}


@router.get("/regime")
def get_regime():
    try:
        from agents.regime_agent import RegimeAgent
        agent = RegimeAgent()
        result = agent.run()
        return result.data or {"regime": "unknown"}
    except Exception as e:
        return {"regime": "unknown", "error": str(e)}


@router.get("/sectors")
def get_sectors():
    results = {}
    try:
        for name, ticker in SECTOR_TICKERS.items():
            t = yf.Ticker(ticker)
            hist = t.history(period="30d")
            if not hist.empty:
                start = float(hist["Close"].iloc[0])
                end = float(hist["Close"].iloc[-1])
                results[name] = round((end - start) / start * 100, 2)
            else:
                results[name] = None
    except Exception as e:
        return {"error": str(e)}
    return results


@router.get("/ltp/{symbol}")
def get_ltp(symbol: str):
    try:
        t = yf.Ticker(symbol.upper() + ".NS")
        hist = t.history(period="2d")
        if hist.empty:
            return {"symbol": symbol, "price": None}
        price = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
        return {
            "symbol": symbol.upper(),
            "price": price,
            "change_pct": round((price - prev) / prev * 100, 2),
        }
    except Exception as e:
        return {"symbol": symbol, "price": None, "error": str(e)}
