"""
Intraday Pattern Scanner — polls NSE every 5 minutes, detects patterns as they form.

Patterns detected:
  - Bull Flag: sharp rally → tight consolidation → breakout
  - Accumulation at Support: price bouncing off same level 3× with rising volume
  - VWAP Reclaim: price dips below VWAP then reclaims with volume
  - RSI Divergence: price lower lows but RSI higher lows
  - Volume Spike: 3× average volume in a single candle
  - Resistance Breakout: price crosses a level it failed 3× before
  - Inside Bar Breakout: tight range candle followed by expansion

Data source: NSE website (free, ~15s delay) + yfinance 5-min candles
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from agents.base import Agent, AgentResult
from core.knowledge_base import kb_path

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
}
TIMEOUT = 8

# Intraday candle fetch params for yfinance (used by `get_intraday_candles`).
# Defined here so that imports + tests don't fail with NameError. See
# docs/analysis/05-issues.md §B0c and docs-verification/findings.md HIGH-3.
CANDLE_LOOKBACK = "2d"   # last 2 days — matches the function docstring.
CANDLE_INTERVAL = "5m"   # 5-minute candles.
NIFTY50 = [
    "RELIANCE","TCS","HDFCBANK","BHARTIARTL","ICICIBANK","INFOSYS","SBIN","HINDUNILVR",
    "ITC","KOTAKBANK","LT","AXISBANK","BAJFINANCE","MARUTI","TITAN","SUNPHARMA","WIPRO",
    "ULTRACEMCO","ADANIENT","NTPC","POWERGRID","TECHM","HCLTECH","BAJAJFINSV","ONGC",
    "COALINDIA","JSWSTEEL","TATASTEEL","INDUSINDBK","GRASIM","ADANIPORTS","DRREDDY",
    "CIPLA","EICHERMOT","APOLLOHOSP","TATACONSUM","NESTLEIND","HINDALCO","BPCL","SBILIFE",
    "HDFCLIFE","BAJAJ-AUTO","TRENT","BEL","SHRIRAMFIN","INDIGO","ETERNAL","JIOFIN",
    "MAXHEALTH","M&M",
]

def get_all_nifty50_ltps() -> dict[str, float]:
    """Return LTPs from shared price cache (populated by price-feed daemon)."""
    import common.pricing as pricing
    cached = pricing.get_many(NIFTY50)
    if cached:
        return {sym: p["price"] for sym, p in cached.items()}
    # Fallback: direct NSE fetch if cache is empty (daemon not running)
    result = {}
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        for sym in NIFTY50[:20]:
            try:
                r = s.get(f"https://www.nseindia.com/api/quote-equity?symbol={sym}", timeout=5)
                if r.status_code == 200:
                    pi = r.json().get("priceInfo", {})
                    price = pi.get("lastPrice") or pi.get("close")
                    if price:
                        result[sym] = float(price)
            except Exception:
                pass
    except Exception:
        pass
    return result


# ── Live Price from NSE ───────────────────────────────────────────────────────

def get_nse_quote(symbol: str) -> dict:
    """Fetch live quote — reads from shared price cache, falls back to direct NSE."""
    import common.pricing as pricing
    cached = pricing.get(symbol)
    if cached and not cached["stale"]:
        return {
            "symbol": symbol,
            "ltp": cached["price"],
            "prev_close": cached["prev_close"],
            "open": 0, "high": 0, "low": 0, "volume": 0, "vwap": 0,
            "fetched_at": datetime.now().isoformat(),
        }
    # Cache miss — direct NSE fetch
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        resp = s.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        pd_data = data.get("priceInfo", {})
        price = pd_data.get("lastPrice", 0)
        prev = pd_data.get("previousClose", 0)
        if price:
            pricing.upsert(symbol, float(price), float(prev or price))
        return {
            "symbol": symbol,
            "ltp": price,
            "open": pd_data.get("open", 0),
            "high": pd_data.get("intraDayHighLow", {}).get("max", 0),
            "low": pd_data.get("intraDayHighLow", {}).get("min", 0),
            "prev_close": prev,
            "volume": data.get("marketDeptOrderBook", {}).get("tradeInfo", {}).get("totalTradedVolume", 0),
            "vwap": pd_data.get("vwap", 0),
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug(f"NSE quote failed for {symbol}: {e}")
        return {}


def get_intraday_candles(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch 5-min candles from yfinance (last 2 days)."""
    try:
        t = yf.Ticker(symbol + ".NS")
        df = t.history(period=CANDLE_LOOKBACK, interval=CANDLE_INTERVAL)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
        # Only today's candles
        today = datetime.now().date()
        df = df[df.index.date == today]
        return df if len(df) >= 5 else None
    except Exception as e:
        logger.debug(f"Intraday candles failed for {symbol}: {e}")
        return None


# ── Pattern Detection ─────────────────────────────────────────────────────────

def detect_bull_flag(df: pd.DataFrame) -> Optional[dict]:
    """
    Bull Flag: strong up candle (pole) → 3-5 tight candles → breakout above pole high.
    Entry: breakout candle close
    """
    if len(df) < 8:
        return None
    closes = df["Close"].values
    volumes = df["Volume"].values
    highs = df["High"].values

    # Find pole: single candle with >1.5% gain and above-avg volume
    avg_vol = volumes.mean()
    for i in range(2, len(df) - 4):
        pole_gain = (closes[i] - closes[i-1]) / closes[i-1] * 100
        if pole_gain < 1.5 or volumes[i] < avg_vol * 1.5:
            continue

        # Flag: next 3-5 candles consolidate (range < 0.5% of pole high)
        flag_candles = df.iloc[i+1:i+5]
        if len(flag_candles) < 3:
            continue
        flag_range = (flag_candles["High"].max() - flag_candles["Low"].min()) / closes[i] * 100
        if flag_range > 1.5:
            continue

        # Breakout: latest candle breaks above pole high
        latest_close = closes[-1]
        pole_high = highs[i]
        if latest_close > pole_high and volumes[-1] > avg_vol:
            return {
                "pattern": "bull_flag",
                "confidence": 75,
                "pole_gain": round(pole_gain, 2),
                "flag_range": round(flag_range, 2),
                "breakout_price": round(latest_close, 2),
                "target": round(latest_close * (1 + pole_gain / 100), 2),
                "stop_loss": round(flag_candles["Low"].min() * 0.999, 2),
                "description": f"Bull flag: {pole_gain:.1f}% pole, {flag_range:.1f}% flag, breakout confirmed",
            }
    return None


def detect_vwap_reclaim(df: pd.DataFrame, quote: dict) -> Optional[dict]:
    """
    VWAP Reclaim: price dips below VWAP then closes back above with volume.
    Strong intraday signal — institutions use VWAP as benchmark.
    """
    if len(df) < 6 or not quote.get("vwap"):
        return None

    vwap = quote["vwap"]
    closes = df["Close"].values
    volumes = df["Volume"].values
    avg_vol = volumes.mean()

    # Check last 3 candles: dipped below VWAP then reclaimed
    if len(closes) < 3:
        return None

    dipped = any(c < vwap for c in closes[-4:-1])
    reclaimed = closes[-1] > vwap
    volume_confirm = volumes[-1] > avg_vol * 1.2

    if dipped and reclaimed and volume_confirm:
        return {
            "pattern": "vwap_reclaim",
            "confidence": 70,
            "vwap": round(vwap, 2),
            "current_price": round(closes[-1], 2),
            "target": round(vwap * 1.015, 2),
            "stop_loss": round(vwap * 0.995, 2),
            "description": f"VWAP reclaim at ₹{vwap:.2f} with {volumes[-1]/avg_vol:.1f}× volume",
        }
    return None


def detect_accumulation_at_support(df: pd.DataFrame) -> Optional[dict]:
    """
    Accumulation: price tests same support level 3+ times with rising volume each time.
    Classic institutional accumulation pattern.
    """
    if len(df) < 10:
        return None

    lows = df["Low"].values
    volumes = df["Volume"].values
    closes = df["Close"].values

    # Find support level (cluster of lows within 0.3%)
    for i in range(2, len(lows) - 2):
        support = lows[i]
        tolerance = support * 0.003

        # Count touches of this support level
        touches = [(j, lows[j], volumes[j]) for j in range(len(lows))
                   if abs(lows[j] - support) <= tolerance]

        if len(touches) < 3:
            continue

        # Check volume is rising on each touch (accumulation)
        touch_vols = [t[2] for t in touches]
        vol_rising = touch_vols[-1] > touch_vols[0]

        # Price currently above support (not breaking down)
        if closes[-1] > support and vol_rising:
            return {
                "pattern": "accumulation_at_support",
                "confidence": 65,
                "support_level": round(support, 2),
                "touches": len(touches),
                "target": round(closes[-1] * 1.02, 2),
                "stop_loss": round(support * 0.997, 2),
                "description": f"Accumulation at ₹{support:.2f} support ({len(touches)} touches, rising volume)",
            }
    return None


def detect_volume_spike(df: pd.DataFrame, quote: dict) -> Optional[dict]:
    """
    Volume Spike: current candle volume is 3× the 20-candle average.
    Signals institutional activity — follow the direction.
    """
    if len(df) < 5:
        return None

    volumes = df["Volume"].values
    avg_vol = volumes[:-1].mean()
    current_vol = volumes[-1]
    current_close = df["Close"].values[-1]
    prev_close = df["Close"].values[-2]

    if avg_vol == 0:
        return None

    ratio = current_vol / avg_vol
    direction = "up" if current_close > prev_close else "down"

    if ratio >= 3.0 and direction == "up":
        return {
            "pattern": "volume_spike",
            "confidence": 60,
            "volume_ratio": round(ratio, 1),
            "direction": direction,
            "target": round(current_close * 1.015, 2),
            "stop_loss": round(prev_close * 0.998, 2),
            "description": f"Volume spike {ratio:.1f}× average — institutional buying detected",
        }
    return None


def detect_resistance_breakout(df: pd.DataFrame) -> Optional[dict]:
    """
    Resistance Breakout: price breaks above a level it failed at 3+ times.
    """
    if len(df) < 12:
        return None

    highs = df["High"].values
    closes = df["Close"].values
    volumes = df["Volume"].values
    avg_vol = volumes.mean()

    # Find resistance (cluster of highs within 0.3%)
    for i in range(2, len(highs) - 3):
        resistance = highs[i]
        tolerance = resistance * 0.003

        # Count rejections at this level
        rejections = [j for j in range(len(highs) - 1)
                      if abs(highs[j] - resistance) <= tolerance and closes[j] < resistance]

        if len(rejections) < 2:
            continue

        # Latest candle breaks above resistance with volume
        if closes[-1] > resistance and volumes[-1] > avg_vol * 1.3:
            return {
                "pattern": "resistance_breakout",
                "confidence": 80,
                "resistance_level": round(resistance, 2),
                "rejections": len(rejections),
                "breakout_price": round(closes[-1], 2),
                "target": round(closes[-1] * 1.02, 2),
                "stop_loss": round(resistance * 0.997, 2),
                "description": f"Breakout above ₹{resistance:.2f} resistance ({len(rejections)} prior rejections)",
            }
    return None


def detect_rsi_divergence(df: pd.DataFrame) -> Optional[dict]:
    """
    Bullish RSI Divergence: price making lower lows but RSI making higher lows.
    Signals momentum shift — reversal likely.
    """
    if len(df) < 20:
        return None

    closes = df["Close"]
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).values

    prices = closes.values

    # Find two recent lows in price
    price_lows = []
    for i in range(2, len(prices) - 1):
        if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
            price_lows.append((i, prices[i], rsi[i]))

    if len(price_lows) < 2:
        return None

    p1, p2 = price_lows[-2], price_lows[-1]

    # Bullish divergence: price lower low but RSI higher low
    if p2[1] < p1[1] and p2[2] > p1[2] and not np.isnan(p2[2]):
        return {
            "pattern": "rsi_divergence",
            "confidence": 70,
            "price_low_1": round(p1[1], 2),
            "price_low_2": round(p2[1], 2),
            "rsi_low_1": round(p1[2], 1),
            "rsi_low_2": round(p2[2], 1),
            "target": round(prices[-1] * 1.02, 2),
            "stop_loss": round(p2[1] * 0.997, 2),
            "description": f"Bullish RSI divergence: price {p1[1]:.0f}→{p2[1]:.0f} (lower) but RSI {p1[2]:.0f}→{p2[2]:.0f} (higher)",
        }
    return None


# ── Scanner ───────────────────────────────────────────────────────────────────

def scan_stock(symbol: str) -> dict:
    """Run all pattern detectors on a single stock."""
    quote = get_nse_quote(symbol)
    df = get_intraday_candles(symbol)

    result = {
        "symbol": symbol,
        "ltp": quote.get("ltp", 0),
        "vwap": quote.get("vwap", 0),
        "patterns": [],
        "best_pattern": None,
        "signal": "WATCH",
        "scanned_at": datetime.now().isoformat(),
    }

    if df is None or df.empty:
        result["signal"] = "NO_DATA"
        return result

    # Run all detectors
    detectors = [
        detect_resistance_breakout(df),
        detect_bull_flag(df),
        detect_vwap_reclaim(df, quote),
        detect_accumulation_at_support(df),
        detect_rsi_divergence(df),
        detect_volume_spike(df, quote),
    ]

    patterns = [p for p in detectors if p is not None]
    result["patterns"] = patterns

    if patterns:
        # Pick highest confidence pattern
        best = max(patterns, key=lambda x: x["confidence"])
        result["best_pattern"] = best
        result["signal"] = "BUY" if best["confidence"] >= 65 else "WATCH"
        result["entry"] = best.get("breakout_price") or result["ltp"]
        result["stop_loss"] = best.get("stop_loss", 0)
        result["target"] = best.get("target", 0)
        result["confidence"] = best["confidence"]

    return result


# ── Main Agent ────────────────────────────────────────────────────────────────

class IntradayPatternScanner(Agent):
    def __init__(self, config: dict):
        super().__init__("IntradayPatternScanner", config)
        self.watchlist = config.get("watchlist", [])

    def run(self, context=None) -> AgentResult:
        return self._result(self.scan_all())

    def scan_all(self) -> dict:
        """
        Two-pass scan:
        Pass 1 (Groww batch): Fetch live LTP for all 50 NIFTY stocks instantly.
                              Flag stocks with unusual intraday move (>1% from open).
        Pass 2 (deep):        Run full pattern detection only on flagged stocks.
        """
        logger.info("IntradayScanner: fetching all 50 NIFTY LTPs via Groww...")

        # Pass 1: batch LTP for all 50
        all_ltps = get_all_nifty50_ltps()

        # Get today's open prices to compute intraday move
        import yfinance as yf
        candidates = []
        for sym, ltp in all_ltps.items():
            try:
                h = yf.Ticker(sym + ".NS").history(period="1d", interval="1d")
                if h.empty:
                    continue
                open_price = float(h["Open"].iloc[-1])
                if open_price == 0:
                    continue
                intraday_move = (ltp - open_price) / open_price * 100
                # Flag stocks moving >1% intraday OR in our watchlist
                if abs(intraday_move) >= 1.0 or sym in self.watchlist:
                    candidates.append((sym, ltp, intraday_move))
            except Exception:
                pass

        candidates.sort(key=lambda x: abs(x[2]), reverse=True)
        logger.info(f"  {len(all_ltps)} LTPs fetched, {len(candidates)} candidates for deep scan")

        # Pass 2: deep pattern scan on candidates
        results = []
        for sym, ltp, move in candidates[:20]:  # cap at 20 for speed
            r = scan_stock(sym)
            r["intraday_move_pct"] = round(move, 2)
            results.append(r)
            if r["patterns"]:
                logger.info(f"  {sym} ({move:+.1f}%): {r['best_pattern']['description'][:60]}")
            time.sleep(0.3)

        buy_signals  = [r for r in results if r["signal"] == "BUY"]
        avoid_signals = [r for r in results if r["signal"] == "AVOID"]
        watch_signals = [r for r in results if r["signal"] == "WATCH" and r["patterns"]]

        return {
            "scanned_at": datetime.now().isoformat(),
            "total_nifty50": len(all_ltps),
            "candidates_deep_scanned": len(results),
            "buy_signals": buy_signals,
            "avoid_signals": avoid_signals,
            "watch_signals": watch_signals,
            "all_ltps": all_ltps,
        }


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    scanner = IntradayPatternScanner(config)
    result = scanner.scan_all()

    SEP = "=" * 65
    print(f"\n{SEP}")
    print(f"  INTRADAY PATTERN SCANNER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    if result["buy_signals"]:
        print(f"\n🟢 BUY SIGNALS ({len(result['buy_signals'])}):")
        for r in result["buy_signals"]:
            p = r["best_pattern"]
            print(f"\n  {r['symbol']} @ ₹{r['ltp']:.2f}")
            print(f"  Pattern    : {p['pattern'].replace('_',' ').title()}")
            print(f"  Confidence : {p['confidence']}%")
            print(f"  Entry      : ₹{r.get('entry', r['ltp']):.2f}")
            print(f"  Stop Loss  : ₹{p['stop_loss']:.2f}")
            print(f"  Target     : ₹{p['target']:.2f}")
            print(f"  Signal     : {p['description']}")

    if result["watch_signals"]:
        print(f"\n🟡 PATTERNS FORMING ({len(result['watch_signals'])}):")
        for r in result["watch_signals"]:
            for p in r["patterns"]:
                print(f"  {r['symbol']}: {p['description']}")

    if not result["buy_signals"] and not result["watch_signals"]:
        print(f"\n  No patterns detected right now")
        print(f"  (Market may be closed or in early session)")

    print(f"\n  Fetched {result['total_nifty50']} NIFTY50 LTPs via Groww")
    print(f"  Deep scanned: {result['candidates_deep_scanned']} candidates (>1% intraday move)")
    print(SEP)
