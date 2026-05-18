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
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from agents.base import Agent, AgentResult
from core.knowledge_base import kb_path
from core import features as F

logger = logging.getLogger(__name__)

# ── Stage 2 constants ─────────────────────────────────────────────────────────

# Cooldown: number of bars to wait before re-firing the same pattern on the
# same symbol. With 5-minute bars this is 30 minutes by default — long enough
# to prevent flooding the decision pipeline with duplicate signals when a
# breakout sits just above its trigger for an hour.
COOLDOWN_BARS = 6

# Built-in fallback "confidence" numbers. Used only if the empirical-stats
# JSON file isn't present (it's produced by `scripts/backtest_intraday_detectors.py`).
# These match the legacy hardcoded values for backward compatibility, but
# mirror exactly the comment in the docstring for each detector.
_DETECTOR_DEFAULT_CONFIDENCE = {
    "bull_flag":               75,
    "vwap_reclaim":            70,
    "accumulation_at_support": 65,
    "volume_spike":            60,
    "resistance_breakout":     80,
    "rsi_divergence":          70,
}

# Path to the backtest-derived stats JSON, populated by Stage 2C's backtester.
_EMPIRICAL_STATS_PATH = Path(__file__).resolve().parent.parent / "models" / "intraday_detector_stats.json"
_empirical_stats_cache: Optional[dict] = None


def _load_empirical_stats() -> dict:
    """Lazy-load the per-detector hit-rate stats produced by the backtester.

    Schema:
        {
          "<detector_name>": {
              "overall":          0.62,                 # all conditions
              "<regime>":         0.71,                 # by regime
              "<regime>_<hour>":  0.78,                 # by regime × hour
              ...
          }, ...
        }

    Returns {} if the file doesn't exist or is malformed — callers fall back
    to `_DETECTOR_DEFAULT_CONFIDENCE`.
    """
    global _empirical_stats_cache
    if _empirical_stats_cache is not None:
        return _empirical_stats_cache
    import json
    if _EMPIRICAL_STATS_PATH.exists():
        try:
            _empirical_stats_cache = json.loads(_EMPIRICAL_STATS_PATH.read_text())
            return _empirical_stats_cache
        except Exception as e:
            logger.warning(f"Failed to load empirical stats: {e}")
    _empirical_stats_cache = {}
    return _empirical_stats_cache


def _empirical_confidence(detector_name: str,
                          regime: str = "unknown",
                          hour: Optional[int] = None) -> int:
    """Empirical hit-rate %, falling back to the legacy hardcoded value if
    no backtest stats are available.

    Lookup order: regime+hour, regime, overall, fallback.
    """
    stats = _load_empirical_stats()
    det = stats.get(detector_name, {})
    fallback = _DETECTOR_DEFAULT_CONFIDENCE.get(detector_name, 50)
    if not det:
        return fallback
    if hour is not None:
        key = f"{regime}_{hour}"
        if key in det:
            return int(round(det[key] * 100))
    if regime in det:
        return int(round(det[regime] * 100))
    if "overall" in det:
        return int(round(det["overall"] * 100))
    return fallback


def _compute_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """Latest ATR as a percentage of latest close. Used to scale per-symbol
    pattern thresholds — a 1.5% bull-flag pole on RELIANCE (ATR≈1%) is a
    different signal from a 1.5% pole on a small-cap (ATR≈3%).
    Returns 1.0 (neutral, no scaling) if ATR isn't computable yet.
    """
    if len(df) < period:
        return 1.0
    atr = F.atr(df["High"], df["Low"], df["Close"], period).iloc[-1]
    close = df["Close"].iloc[-1]
    if pd.isna(atr) or close == 0:
        return 1.0
    return float(atr / close * 100)


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
    """Fetch live LTP for all 50 NIFTY stocks in one Groww API call."""
    try:
        from core.groww_client import get_groww_client
        client = get_groww_client()
        # Groww supports up to 50 per call
        ltps = client.get_ltp(NIFTY50)
        if ltps:
            logger.debug(f"Groww: fetched {len(ltps)} LTPs")
            return ltps
    except Exception as e:
        logger.debug(f"Groww LTP failed: {e}")
    # Fallback: yfinance for watchlist only
    result = {}
    try:
        import yaml
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        for sym in cfg.get("watchlist", []):
            try:
                t = yf.Ticker(sym + ".NS")
                h = t.history(period="1d")
                if not h.empty:
                    result[sym] = float(h["Close"].iloc[-1])
            except Exception:
                pass
    except Exception:
        pass
    return result


# ── Live Price from NSE ───────────────────────────────────────────────────────

def get_nse_quote(symbol: str) -> dict:
    """Fetch live quote from NSE (LTP, volume, OHLC)."""
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        resp = s.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        pd_data = data.get("priceInfo", {})
        return {
            "symbol": symbol,
            "ltp": pd_data.get("lastPrice", 0),
            "open": pd_data.get("open", 0),
            "high": pd_data.get("intraDayHighLow", {}).get("max", 0),
            "low": pd_data.get("intraDayHighLow", {}).get("min", 0),
            "prev_close": pd_data.get("previousClose", 0),
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

def detect_bull_flag(df: pd.DataFrame, atr_pct: float = 1.0) -> Optional[dict]:
    """
    Bull Flag: strong up candle (pole) → 3-5 tight candles → breakout above pole high.
    Entry: breakout candle close.

    Stage 2B: thresholds scale with the symbol's 14-bar ATR. A 1.5% pole on a
    typical large-cap (ATR≈1%) becomes a 1.5×1.0=1.5% threshold; on a more
    volatile name (ATR≈3%) the same multiplier yields 4.5% — preventing
    spurious detections on noisy stocks.
    """
    if len(df) < 8:
        return None
    closes = df["Close"].values
    volumes = df["Volume"].values
    highs = df["High"].values

    pole_gain_min  = 1.5 * atr_pct
    flag_range_max = 1.5 * atr_pct

    # Find pole: single candle with >= adaptive gain and above-avg volume
    avg_vol = volumes.mean()
    for i in range(2, len(df) - 4):
        pole_gain = (closes[i] - closes[i-1]) / closes[i-1] * 100
        if pole_gain < pole_gain_min or volumes[i] < avg_vol * 1.5:
            continue

        # Flag: next 3-5 candles consolidate (range <= adaptive)
        flag_candles = df.iloc[i+1:i+5]
        if len(flag_candles) < 3:
            continue
        flag_range = (flag_candles["High"].max() - flag_candles["Low"].min()) / closes[i] * 100
        if flag_range > flag_range_max:
            continue

        # Breakout: latest candle breaks above pole high
        latest_close = closes[-1]
        pole_high = highs[i]
        if latest_close > pole_high and volumes[-1] > avg_vol:
            return {
                "pattern": "bull_flag",
                "pole_gain": round(pole_gain, 2),
                "flag_range": round(flag_range, 2),
                "atr_pct": round(atr_pct, 3),
                "breakout_price": round(latest_close, 2),
                "target": round(latest_close * (1 + pole_gain / 100), 2),
                "stop_loss": round(flag_candles["Low"].min() * 0.999, 2),
                "description": f"Bull flag: {pole_gain:.1f}% pole, {flag_range:.1f}% flag, breakout (atr={atr_pct:.2f}%)",
            }
    return None


def detect_vwap_reclaim(df: pd.DataFrame, quote: dict) -> Optional[dict]:
    """
    VWAP Reclaim: price dips below VWAP then closes back above with volume.
    Strong intraday signal — institutions use VWAP as benchmark.
    """
def detect_vwap_reclaim(df: pd.DataFrame, quote: dict, atr_pct: float = 1.0) -> Optional[dict]:
    """
    VWAP Reclaim: price dips below VWAP then closes back above with volume.
    Strong intraday signal — institutions use VWAP as benchmark.

    Stage 2B: takes atr_pct for API consistency (no thresholds to scale here —
    volume gate is already adaptive via the rolling mean).
    """
    if len(df) < 6 or not quote.get("vwap"):
        return None

    vwap = quote["vwap"]
    closes = df["Close"].values
    volumes = df["Volume"].values
    avg_vol = volumes.mean()

    if len(closes) < 3:
        return None

    dipped = any(c < vwap for c in closes[-4:-1])
    reclaimed = closes[-1] > vwap
    volume_confirm = volumes[-1] > avg_vol * 1.2

    if dipped and reclaimed and volume_confirm:
        return {
            "pattern": "vwap_reclaim",
            "vwap": round(vwap, 2),
            "current_price": round(closes[-1], 2),
            "target": round(vwap * 1.015, 2),
            "stop_loss": round(vwap * 0.995, 2),
            "description": f"VWAP reclaim at ₹{vwap:.2f} with {volumes[-1]/avg_vol:.1f}× volume",
        }
    return None


def detect_accumulation_at_support(df: pd.DataFrame, atr_pct: float = 1.0) -> Optional[dict]:
    """
    Accumulation: price tests same support level 3+ times with rising volume.

    Stage 2B: support tolerance scales with stock ATR.
    """
    if len(df) < 10:
        return None

    lows = df["Low"].values
    volumes = df["Volume"].values
    closes = df["Close"].values

    # Adaptive tolerance: 0.3% × atr_pct (so a 1% ATR stock gets 0.3% tolerance,
    # a 3% ATR stock gets 0.9% — wider clusters for volatile names).
    tol_pct = 0.003 * atr_pct

    for i in range(2, len(lows) - 2):
        support = lows[i]
        tolerance = support * tol_pct

        touches = [(j, lows[j], volumes[j]) for j in range(len(lows))
                   if abs(lows[j] - support) <= tolerance]

        if len(touches) < 3:
            continue

        touch_vols = [t[2] for t in touches]
        vol_rising = touch_vols[-1] > touch_vols[0]

        if closes[-1] > support and vol_rising:
            return {
                "pattern": "accumulation_at_support",
                "support_level": round(support, 2),
                "touches": len(touches),
                "atr_pct": round(atr_pct, 3),
                "target": round(closes[-1] * 1.02, 2),
                "stop_loss": round(support * 0.997, 2),
                "description": f"Accumulation at ₹{support:.2f} support ({len(touches)} touches, rising volume, atr={atr_pct:.2f}%)",
            }
    return None


def detect_volume_spike(df: pd.DataFrame, quote: dict, atr_pct: float = 1.0) -> Optional[dict]:
    """
    Volume Spike: current candle volume is 3× the 20-candle average.

    Stage 2B: takes atr_pct for API consistency. The 3× volume gate is
    already adaptive in nature (it's a ratio, not an absolute level).
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
            "volume_ratio": round(ratio, 1),
            "direction": direction,
            "target": round(current_close * 1.015, 2),
            "stop_loss": round(prev_close * 0.998, 2),
            "description": f"Volume spike {ratio:.1f}× average — institutional buying detected",
        }
    return None


def detect_resistance_breakout(df: pd.DataFrame, atr_pct: float = 1.0) -> Optional[dict]:
    """
    Resistance Breakout: price breaks above a level it failed at 3+ times.

    Stage 2B: cluster tolerance scales with stock ATR.
    """
    if len(df) < 12:
        return None

    highs = df["High"].values
    closes = df["Close"].values
    volumes = df["Volume"].values
    avg_vol = volumes.mean()

    tol_pct = 0.003 * atr_pct

    for i in range(2, len(highs) - 3):
        resistance = highs[i]
        tolerance = resistance * tol_pct

        rejections = [j for j in range(len(highs) - 1)
                      if abs(highs[j] - resistance) <= tolerance and closes[j] < resistance]

        if len(rejections) < 2:
            continue

        if closes[-1] > resistance and volumes[-1] > avg_vol * 1.3:
            return {
                "pattern": "resistance_breakout",
                "resistance_level": round(resistance, 2),
                "rejections": len(rejections),
                "atr_pct": round(atr_pct, 3),
                "breakout_price": round(closes[-1], 2),
                "target": round(closes[-1] * 1.02, 2),
                "stop_loss": round(resistance * 0.997, 2),
                "description": f"Breakout above ₹{resistance:.2f} resistance ({len(rejections)} prior rejections, atr={atr_pct:.2f}%)",
            }
    return None


def detect_rsi_divergence(df: pd.DataFrame, atr_pct: float = 1.0) -> Optional[dict]:
    """
    Bullish RSI Divergence: price making lower lows but RSI making higher lows.

    Stage 2B: takes atr_pct for API consistency (no scalable thresholds here).
    """
    if len(df) < 20:
        return None

    closes = df["Close"]
    # Canonical Wilder's RSI from core.features (Stage 0).
    rsi = F.rsi(closes, period=14).values
    prices = closes.values

    price_lows = []
    for i in range(2, len(prices) - 1):
        if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
            price_lows.append((i, prices[i], rsi[i]))

    if len(price_lows) < 2:
        return None

    p1, p2 = price_lows[-2], price_lows[-1]

    if p2[1] < p1[1] and p2[2] > p1[2] and not np.isnan(p2[2]):
        return {
            "pattern": "rsi_divergence",
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

def scan_stock(symbol: str,
               cooldown_state: Optional[dict] = None,
               regime: str = "unknown") -> dict:
    """Run all pattern detectors on a single stock.

    Stage 2A/B/C wired in here:
      - Adaptive thresholds: atr_pct computed once and passed to each detector
        so the per-symbol detection sensitivity scales with the stock's
        recent volatility.
      - Cooldown: a pattern that already fired for this symbol within the
        last COOLDOWN_BARS bars is suppressed. State is held by the caller
        (IntradayPatternScanner instance) so it survives across scan cycles.
      - Empirical confidence: each fired pattern's `confidence` field is set
        from the backtest-derived stats file if present, else falls back to
        the legacy hardcoded value. Cuts the dependency on guessed numbers.
    """
    quote = get_nse_quote(symbol)
    df = get_intraday_candles(symbol)

    result = {
        "symbol": symbol,
        "ltp": quote.get("ltp", 0),
        "vwap": quote.get("vwap", 0),
        "patterns": [],
        "best_pattern": None,
        "signal": "WATCH",
        "regime": regime,
        "scanned_at": datetime.now().isoformat(),
    }

    if df is None or df.empty:
        result["signal"] = "NO_DATA"
        return result

    atr_pct = _compute_atr_pct(df)
    last_bar_ts = df.index[-1]
    hour = last_bar_ts.hour
    result["atr_pct"] = round(atr_pct, 3)

    # Run all detectors — each takes atr_pct and (optionally) the live quote.
    raw = [
        detect_resistance_breakout(df, atr_pct=atr_pct),
        detect_bull_flag(df, atr_pct=atr_pct),
        detect_vwap_reclaim(df, quote, atr_pct=atr_pct),
        detect_accumulation_at_support(df, atr_pct=atr_pct),
        detect_rsi_divergence(df, atr_pct=atr_pct),
        detect_volume_spike(df, quote, atr_pct=atr_pct),
    ]

    # Cooldown filter + empirical confidence annotation.
    # NOTE: `cooldown_state or {}` is wrong here — an empty dict is falsy
    # in Python, so that idiom would return a fresh dict and lose mutations.
    if cooldown_state is None:
        sym_cooldown: dict = {}
    else:
        sym_cooldown = cooldown_state.setdefault(symbol, {})

    patterns = []
    for r in raw:
        if r is None:
            continue
        name = r["pattern"]

        # Cooldown: skip if same pattern fired recently for this symbol.
        last_fire_ts = sym_cooldown.get(name)
        if last_fire_ts is not None:
            try:
                bars_since = max(
                    0,
                    df.index.get_indexer([last_bar_ts])[0]
                    - df.index.get_indexer([last_fire_ts])[0],
                )
            except Exception:
                bars_since = 0
            if bars_since < COOLDOWN_BARS:
                continue   # suppress re-fire
        sym_cooldown[name] = last_bar_ts

        # Empirical hit-rate as confidence (fallback to legacy default).
        r["confidence"] = _empirical_confidence(name, regime=regime, hour=hour)
        patterns.append(r)

    result["patterns"] = patterns

    if patterns:
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
        # Per-instance cooldown state survives across scan_all() calls.
        # Shape: {symbol: {pattern_name: last_fire_timestamp}}
        self._cooldown_state: dict = {}
        self._current_regime: str = "unknown"

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
            r = scan_stock(sym, cooldown_state=self._cooldown_state,
                            regime=self._current_regime)
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
