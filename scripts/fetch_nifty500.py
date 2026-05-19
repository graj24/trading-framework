"""
Fetch price history for all Nifty 500 stocks into stocks/<SYMBOL>/price_history.parquet.

Usage:
    PYTHONPATH=/app python scripts/fetch_nifty500.py
    PYTHONPATH=/app python scripts/fetch_nifty500.py --workers 4  # parallel
"""
from __future__ import annotations

import argparse
import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Nifty 500 constituents (as of 2025). Source: NSE India.
NIFTY_500 = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK","SBIN","INFOSYS",
    "HINDUNILVR","WIPRO","HCLTECH","INFY","BAJFINANCE","MARUTI","LT","AXISBANK",
    "KOTAKBANK","SUNPHARMA","TITAN","ULTRACEMCO","NTPC","ONGC","COALINDIA",
    "POWERGRID","BAJAJFINSV","ASIANPAINT","TECHM","ADANIENT","ADANIPORTS",
    "JSWSTEEL","TATASTEEL","HINDALCO","CIPLA","DIVISLAB","DRREDDY","GRASIM",
    "APOLLOHOSP","HDFCLIFE","SBILIFE","ITC","NESTLEIND","BRITANNIA","EICHERMOT",
    "HEROMOTOCO","INDUSINDBK","BAJAJ_AUTO","BPCL","TATACONSUM","INDIGO","ETERNAL",
    "M&M","TATAMOTORS","TATAPOWER","TORNTPHARM","LUPIN","BIOCON","AUROPHARMA",
    "CADILAHC","IPCALAB","SUNPHARMA","GLAXO","ABBOTINDIA","PFIZER","SANOFI",
    "ALKEM","LAURUSLABS","GRANULES","NATCOPHARM","AJANTPHARM","MANKIND",
    "ZYDUSLIFE","TORNTPHARM","JBCHEPHARM",
    "HCLTECH","LTIM","MPHASIS","COFORGE","PERSISTENT","HEXAWARE","KPITTECH",
    "TATAELXSI","MASTEK","ZENSAR","NIITTECH","RAMSYSTEMS","CYIENT","SONACOMS",
    "TANLA","NEWGEN","INTELLECT","HAPPSTMNDS",
    "ICICIBANK","HDFCBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK","BANKBARODA",
    "CANARABANK","PNB","UNIONBANK","IDFCFIRSTB","FEDERALBNK","AUBANK","BANDHANBNK",
    "RBLBANK","YESBANK","DCBBANK","KARURVYSYA","LAKSHVILAS","CITYUNIONB",
    "BAJFINANCE","BAJAJFINSV","HDFCAMC","LICGF","NIPPONMF","MUTHOOTFIN",
    "MANAPPURAM","CHOLAFIN","LTFH","PNBHOUSING","CANFINHOME","HOMEFIRST",
    "AAVAS","APTUS","SBFC",
    "RELIANCE","ONGC","BPCL","IOC","HINDPETRO","MRPL","GAIL","OIL","GSPL",
    "IGL","MGL","ATGL","PETRONET","AEGISCHEM","DEEPAKNTR","GNFC","COROMANDEL",
    "PIIND","RALLIS","BAYER","SUMICHEM","DHANUKA","INSECTI",
    "TITAN","KALYAN","SENCO","PCJEWELLER","RAJESHEXPO",
    "ASIANPAINT","BERGER","KANSAINER","AKZONOBEL","SHEENLAC",
    "ULTRACEMCO","AMBUJACEM","ACC","RAMCOCEM","HEIDELBERG","JKCEMENT","DALMIA",
    "SHREECEM","BIRLACORPN","ORIENT",
    "JSWSTEEL","TATASTEEL","HINDALCO","SAIL","NMDC","MOIL","WELCORP",
    "RATNAMANI","JINDALSTEL","JSPL","APL","IOLCP","SHYAMMETL",
    "MARUTI","TATAMOTORS","M&M","BAJAJ_AUTO","EICHERMOT","HEROMOTOCO",
    "TVSMOTORS","ASHOKLEY","TVSMOTOR","MOTHERSON","BOSCHLTD","BHARATFORG",
    "SUNDRMFAST","EXIDEIND","AMARAJABAT","APOLLOTYRE","CEATLTD","MRF",
    "BALKRISIND","TIINDIA","ZFCVINDIA","ENDURANCE","GABRIEL","SUPRAJIT",
    "NTPC","POWERGRID","TATAPOWER","ADANIPOWER","ADANIGREEN","TORNTPOWER",
    "CESC","NHPC","SJVN","THERMAX","BHEL","ABB","SIEMENS","HAVELLS",
    "VOLTAS","BLUESTAR","WHIRLPOOL","SYMPHONY","CROMPTON","BAJAJELEC",
    "POLYCAB","KEI","FINOLEX","HBLPOWER","IEXINDIA",
    "DLF","GODREJPROP","PRESTIGE","OBEROIRLTY","BRIGADE","MAHLIFE",
    "SOBHA","LODHA","SIGNATURE","NCLIND",
    "HINDUNILVR","ITC","NESTLEIND","BRITANNIA","TATACONSUM","COLPAL",
    "GODREJCP","MARICO","DABUR","EMAMILTD","PGHH","GILLETTE","JYOTHYLAB",
    "BAJAJCON","VSTIND","GODFRYPHLP","ITC",
    "CIPLA","SUNPHARMA","DRREDDY","DIVISLAB","LUPIN","AUROPHARMA","BIOCON",
    "TORNTPHARM","IPCALAB","ALKEM","LAURUSLABS","GRANULES","ABBOTINDIA",
    "PFIZER","GLAXO","SANOFI","NATCOPHARM","AJANTPHARM","MANKIND","ZYDUSLIFE",
    "APOLLOHOSP","FORTIS","MAXHEALTH","NARAYANA","METROPOLIS","THYROCARE",
    "DRLABANST","LALPATHLAB","VIJAYA","KIMS","RAINBOW","MEDANTA",
    "IRCTC","INDHOTEL","LEMONTRE","CHALET","MAHINDRAHOLIDAYS","EIHOTEL",
    "INDIAMART","JUSTDIAL","NAUKRI","POLICYBZR","ZOMATO","SWIGGY","PAYTM",
    "NYKAA","CARTRADE","MOBIKWIK",
    "COALINDIA","NMDC","MOIL","GMRINFRA","GVK","IRB","NHAI","CONCOR",
    "ADANIPORTS","MUNDRAPORT","APSEZ","MAHINDRA",
    "ABCAPL","ANGELONE","BSE","MCX","CDSL","CAMS","KFIN","NSDL",
    "LICHSGFIN","GICRE","NIACL","STARHEALTH","GODIGIT",
    "INDIGOPNTS","SAREGAMA","PVRINOX","INOX","TIPS","BALAJITELE",
    "NETWORK18","TVTODAY","JAGRAN","DBCORP","HTMEDIA",
    "ABFRL","TRENT","SHOPERSTOP","VMART","SPENCERS","VEDANT",
    "PAGE","CANTABIL","KEWAL","ZODIAC",
    "WIPRO","HEXAWARE","NIITLTD","APTECH","CRISIL","ICRA","CARE",
    "TEAMLEASE","QUESS","SIS","SECURITAS",
    "PIDILITIND","ASTRAL","SUPREMEIND","FINOLEX","NILKAMAL","PRINCEPIPE",
    "UFLEX","HUHTAMAKI","JFLLIFE",
    "ZEEL","SUNTV","TVTODAY","NDTVPVT","DBREALTY",
]

# Deduplicate while preserving order
seen = set()
NIFTY_500_UNIQUE = []
for s in NIFTY_500:
    if s not in seen:
        seen.add(s)
        NIFTY_500_UNIQUE.append(s)


def fetch_one(symbol: str, history_years: int = 5) -> tuple[str, str]:
    """Fetch price history for a single symbol. Returns (symbol, status)."""
    from datetime import datetime, timedelta
    from core.nse_historical import fetch_history

    path = Path("stocks") / symbol / "price_history.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Incremental: only fetch missing data
    import pandas as pd
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            last_date = existing.index.max()
            if hasattr(last_date, "to_pydatetime"):
                last_date = last_date.to_pydatetime()
            start = last_date + timedelta(days=1)
            if start.date() >= datetime.now().date():
                return symbol, f"up-to-date ({len(existing)} rows)"
        except Exception:
            existing = None
            start = datetime.now() - timedelta(days=history_years * 365)
    else:
        existing = None
        start = datetime.now() - timedelta(days=history_years * 365)

    try:
        df = fetch_history(symbol, start=start, end=datetime.now())
    except Exception as e:
        return symbol, f"error: {e}"

    if df.empty:
        return symbol, "empty"

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    if existing is not None and not existing.empty:
        if existing.index.tz is not None:
            existing.index = existing.index.tz_localize(None)
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()

    df.to_parquet(path)
    return symbol, f"{len(df)} rows"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel workers (default: 3, keep low to avoid NSE rate limiting)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between requests in seconds (default: 1.0)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    symbols = NIFTY_500_UNIQUE
    print(f"Fetching price history for {len(symbols)} Nifty 500 stocks...")
    print(f"Workers: {args.workers}, Delay: {args.delay}s\n")

    ok, errors, skipped = 0, 0, 0

    if args.workers == 1:
        for i, symbol in enumerate(symbols, 1):
            sym, status = fetch_one(symbol)
            tag = "✓" if "rows" in status or "up-to-date" in status else "✗"
            print(f"[{i:3d}/{len(symbols)}] {tag} {sym:<15} {status}")
            if "error" in status or status == "empty":
                errors += 1
            elif "up-to-date" in status:
                skipped += 1
            else:
                ok += 1
            if "error" not in status and status != "empty":
                time.sleep(args.delay)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(fetch_one, s): s for s in symbols}
            for i, fut in enumerate(as_completed(futures), 1):
                sym, status = fut.result()
                tag = "✓" if "rows" in status or "up-to-date" in status else "✗"
                print(f"[{i:3d}/{len(symbols)}] {tag} {sym:<15} {status}")
                if "error" in status or status == "empty":
                    errors += 1
                elif "up-to-date" in status:
                    skipped += 1
                else:
                    ok += 1
                time.sleep(args.delay / args.workers)

    print(f"\nDone. Fetched: {ok}  Skipped (up-to-date): {skipped}  Errors: {errors}")
    print("Now delete stocks/_market_data.* and retrain:")
    print("  rm -f stocks/_market_data.parquet stocks/_market_data.meta")
    print("  PYTHONPATH=/app python models/ml_model.py train")


if __name__ == "__main__":
    main()
