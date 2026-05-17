"""
fetch_universe.py — Download price history for a large global stock universe.

Universes:
  - Nifty 50 (NSE India)
  - S&P 500 (US)
  - NASDAQ 100 (US)
  - FTSE 100 (UK)
  - Nikkei 225 top 30 (Japan)
  - Hang Seng top 20 (HK)
  - DAX 40 (Germany)

Usage:
  python3 fetch_universe.py           # fetch all
  python3 fetch_universe.py nifty     # fetch only Nifty 50
  python3 fetch_universe.py sp500     # fetch only S&P 500
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

HISTORY_YEARS = 5
START = (datetime.now() - timedelta(days=HISTORY_YEARS * 365)).strftime("%Y-%m-%d")
STOCKS_DIR = Path("stocks")

# ── Universe definitions ──────────────────────────────────────────────────────

NIFTY50 = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
    "HINDUNILVR.NS","SBIN.NS","BHARTIARTL.NS","ITC.NS","KOTAKBANK.NS",
    "LT.NS","AXISBANK.NS","ASIANPAINT.NS","MARUTI.NS","TITAN.NS",
    "SUNPHARMA.NS","ULTRACEMCO.NS","BAJFINANCE.NS","WIPRO.NS","HCLTECH.NS",
    "NESTLEIND.NS","POWERGRID.NS","NTPC.NS","TECHM.NS","INDUSINDBK.NS",
    "TATAMOTORS.NS","BAJAJFINSV.NS","ONGC.NS","COALINDIA.NS","ADANIENT.NS",
    "ADANIPORTS.NS","DIVISLAB.NS","DRREDDY.NS","EICHERMOT.NS","GRASIM.NS",
    "HEROMOTOCO.NS","HINDALCO.NS","JSWSTEEL.NS","M&M.NS","SBILIFE.NS",
    "TATACONSUM.NS","TATASTEEL.NS","CIPLA.NS","APOLLOHOSP.NS","BAJAJ-AUTO.NS",
    "BPCL.NS","BRITANNIA.NS","HDFCLIFE.NS","INDIGO.NS","ETERNAL.NS",
]

SP500_SAMPLE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","JPM","V",
    "UNH","XOM","JNJ","WMT","MA","PG","HD","CVX","MRK","ABBV",
    "LLY","AVGO","PEP","KO","COST","TMO","MCD","ACN","BAC","CRM",
    "NFLX","AMD","ADBE","QCOM","TXN","NEE","PM","RTX","HON","AMGN",
    "IBM","GE","CAT","SPGI","INTU","ISRG","BKNG","AXP","GS","BLK",
]

NASDAQ100_EXTRA = [
    "PANW","SNPS","CDNS","MRVL","KLAC","LRCX","AMAT","ASML","MU","INTC",
    "PYPL","ABNB","DDOG","ZS","CRWD","SNOW","TEAM","WDAY","OKTA","VEEV",
]

FTSE100_SAMPLE = [
    "HSBA.L","BP.L","SHEL.L","AZN.L","ULVR.L","GSK.L","RIO.L","LLOY.L",
    "BARC.L","VOD.L","BT-A.L","GLEN.L","AAL.L","PRU.L","NG.L",
    "REL.L","CPG.L","EXPN.L","DGE.L","RKT.L",
]

NIKKEI_SAMPLE = [
    "7203.T","6758.T","9984.T","8306.T","6861.T","4063.T","9432.T",
    "7974.T","6902.T","8035.T","4502.T","9433.T","6954.T","7267.T","8316.T",
]

HANGSENG_SAMPLE = [
    "0700.HK","0941.HK","1299.HK","0005.HK","2318.HK","0388.HK",
    "1398.HK","3988.HK","0883.HK","2628.HK","0016.HK","0011.HK",
    "1810.HK","9988.HK","0027.HK",
]

DAX_SAMPLE = [
    "SAP.DE","SIE.DE","ALV.DE","MRK.DE","DTE.DE","BAYN.DE","BMW.DE",
    "MBG.DE","BAS.DE","ADS.DE","VOW3.DE","DBK.DE","RWE.DE","HEN3.DE","IFX.DE",
]

UNIVERSES = {
    "nifty":   NIFTY50,
    "sp500":   SP500_SAMPLE,
    "nasdaq":  NASDAQ100_EXTRA,
    "ftse":    FTSE100_SAMPLE,
    "nikkei":  NIKKEI_SAMPLE,
    "hk":      HANGSENG_SAMPLE,
    "dax":     DAX_SAMPLE,
}

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_symbol(ticker: str) -> str:
    """Fetch and save price history for one ticker. Returns status string."""
    # Derive a clean symbol name for the directory
    symbol = ticker.replace(".NS","").replace(".L","").replace(".T","") \
                   .replace(".HK","").replace(".DE","").replace("-","_")

    path = STOCKS_DIR / symbol / "price_history.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Incremental: only fetch new data if file exists
        if path.exists():
            existing = pd.read_parquet(path)
            last_date = pd.to_datetime(existing.index.max())
            start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if start >= datetime.now().strftime("%Y-%m-%d"):
                return f"{symbol}: up to date"
            df_new = yf.Ticker(ticker).history(start=start, interval="1d")
            if not df_new.empty:
                df = pd.concat([existing, df_new])
                df = df[~df.index.duplicated(keep="last")]
            else:
                return f"{symbol}: no new data"
        else:
            df = yf.Ticker(ticker).history(start=START, interval="1d")

        if df.empty or len(df) < 100:
            return f"{symbol}: insufficient data ({len(df)} rows)"

        df.to_parquet(path)
        return f"{symbol}: {len(df)} rows saved"

    except Exception as e:
        return f"{symbol}: FAILED — {e}"


def fetch_universe(name: str, tickers: list[str]):
    print(f"\n{'='*55}")
    print(f"  Fetching {name.upper()} ({len(tickers)} stocks)")
    print(f"{'='*55}")
    ok, failed = 0, 0
    for i, ticker in enumerate(tickers):
        status = fetch_symbol(ticker)
        icon = "✅" if "rows" in status or "up to date" in status else "❌"
        print(f"  {icon} {status}")
        if "rows" in status or "up to date" in status:
            ok += 1
        else:
            failed += 1
        # Rate limit: small pause every 10 requests
        if (i + 1) % 10 == 0:
            time.sleep(1)
    print(f"\n  Done: {ok} OK, {failed} failed")
    return ok


if __name__ == "__main__":
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    total_ok = 0
    if target == "all":
        for name, tickers in UNIVERSES.items():
            total_ok += fetch_universe(name, tickers)
    elif target in UNIVERSES:
        total_ok += fetch_universe(target, UNIVERSES[target])
    else:
        print(f"Unknown universe '{target}'. Options: {list(UNIVERSES.keys())} or 'all'")
        sys.exit(1)

    # Count total stocks available for training
    all_stocks = list(STOCKS_DIR.glob("*/price_history.parquet"))
    print(f"\n{'='*55}")
    print(f"  Total stocks available for training: {len(all_stocks)}")
    print(f"  Run: python3 ml_model.py train   to retrain on all data")
    print(f"{'='*55}\n")
