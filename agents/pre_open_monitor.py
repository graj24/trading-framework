"""
Pre-Open Monitor — catches gap-ups and gap-downs BEFORE market opens.

NSE Pre-Open Session: 9:00 AM - 9:15 AM IST
  - 9:00-9:08: Order collection
  - 9:08-9:12: Price discovery
  - 9:12-9:15: Buffer / confirmation
  - 9:15: Market opens

This agent runs at 9:00 AM and again at 9:08 AM to:
1. Fetch pre-open indicated prices from NSE
2. Compute gap % vs previous close
3. Cross-reference with:
   - Earnings calendar signals (result filed overnight?)
   - News sentiment (any breaking news?)
   - Technical levels (gap above resistance = breakout?)
   - Volume in pre-open (high volume = conviction)
4. Generate TRADE / SKIP signal with entry, SL, target

A gap-up with:
  - Earnings beat catalyst → STRONG BUY
  - High pre-open volume → BUY
  - No catalyst → WATCH (could be fake gap, fades quickly)

A gap-down with:
  - Earnings miss → AVOID / SHORT
  - No catalyst → WATCH for reversal
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
import yfinance as yf
import pandas as pd

from agents.base import Agent, AgentResult
from core.knowledge_base import read_kb, kb_path

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
TIMEOUT = 10

# Gap thresholds
GAP_UP_THRESHOLD   = 1.5   # % — gap-up worth analyzing
GAP_DOWN_THRESHOLD = -1.5  # % — gap-down worth analyzing
STRONG_GAP         = 4.0   # % — strong gap, high conviction


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
    return s


# ── Pre-Open Price Fetching ───────────────────────────────────────────────────

def fetch_preopen_prices() -> dict[str, dict]:
    """
    Fetch NSE pre-open session prices for all NIFTY 50 stocks.
    Returns {symbol: {preopen_price, prev_close, gap_pct, volume}}
    """
    results = {}
    try:
        s = _nse_session()
        url = "https://www.nseindia.com/api/market-data-pre-open?key=NIFTY"
        resp = s.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()

        for item in data.get("data", []):
            meta = item.get("metadata", {})
            symbol = meta.get("symbol", "")
            preopen = meta.get("finalPrice", 0) or meta.get("iep", 0)
            prev_close = meta.get("previousClose", 0) or meta.get("lastPrice", 0)
            volume = meta.get("totalTradedVolume", 0) or item.get("detail", {}).get("preOpenMarket", {}).get("totalTradedVolume", 0)

            if symbol and preopen and prev_close:
                gap_pct = (preopen - prev_close) / prev_close * 100
                results[symbol] = {
                    "symbol": symbol,
                    "preopen_price": round(preopen, 2),
                    "prev_close": round(prev_close, 2),
                    "gap_pct": round(gap_pct, 2),
                    "preopen_volume": volume,
                    "fetched_at": datetime.now().isoformat(),
                }
    except Exception as e:
        logger.debug(f"NSE pre-open fetch failed: {e}")

    # Fallback: use yfinance pre-market data for watchlist
    if not results:
        results = _fetch_preopen_yfinance()

    return results


def _fetch_preopen_yfinance(symbols: Optional[list[str]] = None) -> dict[str, dict]:
    """
    Fallback: estimate pre-open gap using yfinance.
    Uses the difference between current price and previous close.
    """
    results = {}
    if not symbols:
        # Load from config
        try:
            import yaml
            with open("config.yaml") as f:
                cfg = yaml.safe_load(f)
            symbols = cfg.get("watchlist", [])
        except Exception:
            return results

    for symbol in symbols:
        try:
            t = yf.Ticker(symbol + ".NS")
            hist = t.history(period="2d", interval="1d")
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
                current = float(hist["Close"].iloc[-1])
                gap_pct = (current - prev_close) / prev_close * 100
                results[symbol] = {
                    "symbol": symbol,
                    "preopen_price": round(current, 2),
                    "prev_close": round(prev_close, 2),
                    "gap_pct": round(gap_pct, 2),
                    "preopen_volume": int(hist["Volume"].iloc[-1]),
                    "fetched_at": datetime.now().isoformat(),
                    "source": "yfinance_fallback",
                }
        except Exception:
            pass

    return results


# ── Gap Analysis ──────────────────────────────────────────────────────────────

def analyze_gap(symbol: str, gap_data: dict) -> dict:
    """
    Deep analysis of a gap — is it worth trading?

    Checks:
    1. Gap size and direction
    2. Earnings catalyst (from KB)
    3. News sentiment (from KB)
    4. Technical context (gap above/below key levels)
    5. Historical gap behavior for this stock
    6. Pre-open volume vs average
    """
    gap_pct = gap_data.get("gap_pct", 0)
    preopen_price = gap_data.get("preopen_price", 0)
    preopen_vol = gap_data.get("preopen_volume", 0)

    analysis = {
        "symbol": symbol,
        "gap_pct": gap_pct,
        "gap_type": "gap_up" if gap_pct > 0 else "gap_down",
        "gap_strength": "strong" if abs(gap_pct) >= STRONG_GAP else "moderate" if abs(gap_pct) >= GAP_UP_THRESHOLD else "weak",
        "catalysts": [],
        "risks": [],
        "trade_signal": "SKIP",
        "confidence": 0,
        "entry": preopen_price,
        "stop_loss": 0.0,
        "target": 0.0,
        "reasoning": "",
    }

    score = 0

    # 1. Earnings catalyst
    event_data = read_kb(symbol, "event_reactions.json")
    premarket_signals = event_data.get("premarket_signals", [])
    cutoff = datetime.now() - timedelta(hours=12)
    recent_signals = [
        s for s in premarket_signals
        if datetime.fromisoformat(s.get("generated_at", "2000-01-01")) > cutoff
    ]

    if recent_signals:
        latest = recent_signals[-1]
        verdict = latest.get("verdict", "")
        action = latest.get("action", "")
        if "BUY" in action and gap_pct > 0:
            analysis["catalysts"].append(f"Earnings {verdict} filed overnight")
            score += 40 if action == "STRONG_BUY" else 25
        elif "AVOID" in action and gap_pct < 0:
            analysis["risks"].append(f"Earnings {verdict} — gap-down confirmed")
            score -= 30

    # 2. News sentiment
    news_kb = read_kb(symbol, "news_history.json")
    recent_news = [
        n for n in news_kb.get("news", [])
        if n.get("fetched_at", "") > (datetime.now() - timedelta(hours=12)).isoformat()
    ]
    if recent_news:
        avg_sentiment = sum(n.get("sentiment", 0) for n in recent_news) / len(recent_news)
        if avg_sentiment > 0.3 and gap_pct > 0:
            analysis["catalysts"].append(f"Positive news sentiment ({avg_sentiment:+.2f})")
            score += 15
        elif avg_sentiment < -0.3 and gap_pct < 0:
            analysis["risks"].append(f"Negative news sentiment ({avg_sentiment:+.2f})")
            score -= 15

    # 3. Technical context — is gap above resistance?
    path = kb_path(symbol) / "price_history.parquet"
    if path.exists():
        try:
            price_df = pd.read_parquet(path).sort_index()
            price_df.index = pd.to_datetime(price_df.index, utc=True).tz_localize(None)
            recent_prices = price_df["Close"].tail(252)

            # Check if gap is above 52-week high (breakout)
            high_52w = float(recent_prices.max())
            if gap_pct > 0 and preopen_price > high_52w * 0.98:
                analysis["catalysts"].append(f"Near/above 52W high (₹{high_52w:.0f}) — breakout territory")
                score += 20

            # Average volume
            avg_vol = float(price_df["Volume"].tail(20).mean())
            if avg_vol > 0 and preopen_vol > avg_vol * 1.5:
                analysis["catalysts"].append(f"Pre-open volume {preopen_vol/avg_vol:.1f}× average — conviction")
                score += 15
            elif preopen_vol < avg_vol * 0.3:
                analysis["risks"].append("Low pre-open volume — gap may fade")
                score -= 10

            # Historical gap behavior: how often does this stock hold its gap?
            gap_history = _compute_gap_history(price_df)
            if gap_history:
                hold_rate = gap_history.get("hold_rate", 0.5)
                if gap_pct > 0 and hold_rate > 0.6:
                    analysis["catalysts"].append(f"Historically holds gap-ups {hold_rate:.0%} of the time")
                    score += 10
                elif gap_pct > 0 and hold_rate < 0.4:
                    analysis["risks"].append(f"Historically fades gap-ups {1-hold_rate:.0%} of the time")
                    score -= 10

        except Exception as e:
            logger.debug(f"Technical context failed for {symbol}: {e}")

    # 4. Gap size scoring
    if abs(gap_pct) >= STRONG_GAP:
        score += 15
    elif abs(gap_pct) >= GAP_UP_THRESHOLD:
        score += 8

    # 5. Generate trade signal
    if gap_pct > GAP_UP_THRESHOLD and score >= 40:
        analysis["trade_signal"] = "BUY"
        analysis["confidence"] = min(95, score)
        # Entry: at open (pre-open price + small buffer)
        analysis["entry"] = round(preopen_price * 1.002, 2)
        # SL: below pre-open low (use gap fill as SL)
        analysis["stop_loss"] = round(gap_data["prev_close"] * 1.005, 2)  # just above prev close
        # Target: 2× the gap size
        analysis["target"] = round(preopen_price * (1 + abs(gap_pct) / 100), 2)
        analysis["reasoning"] = f"Gap-up {gap_pct:+.1f}% with {len(analysis['catalysts'])} catalyst(s): {'; '.join(analysis['catalysts'][:2])}"

    elif gap_pct > STRONG_GAP and score >= 25:
        analysis["trade_signal"] = "BUY"
        analysis["confidence"] = min(75, score)
        analysis["entry"] = round(preopen_price * 1.002, 2)
        analysis["stop_loss"] = round(gap_data["prev_close"] * 1.005, 2)
        analysis["target"] = round(preopen_price * (1 + abs(gap_pct) / 100), 2)
        analysis["reasoning"] = f"Strong gap-up {gap_pct:+.1f}% — momentum play"

    elif gap_pct < GAP_DOWN_THRESHOLD and score <= -20:
        analysis["trade_signal"] = "AVOID"
        analysis["confidence"] = min(85, abs(score))
        analysis["reasoning"] = f"Gap-down {gap_pct:+.1f}% with risks: {'; '.join(analysis['risks'][:2])}"

    else:
        analysis["trade_signal"] = "WATCH"
        analysis["confidence"] = 30
        analysis["reasoning"] = f"Gap {gap_pct:+.1f}% — insufficient catalyst or conviction to trade"

    return analysis


def _compute_gap_history(price_df: pd.DataFrame) -> dict:
    """
    Compute how often this stock holds its gap-ups historically.
    A gap is "held" if close > open on the gap day.
    """
    try:
        opens = price_df["Open"]
        closes = price_df["Close"]
        prev_closes = price_df["Close"].shift(1)

        gaps = (opens - prev_closes) / prev_closes * 100
        gap_up_days = gaps[gaps > 1.5]

        if len(gap_up_days) < 5:
            return {}

        held = sum(1 for idx in gap_up_days.index if closes[idx] >= opens[idx])
        return {
            "total_gap_ups": len(gap_up_days),
            "held": held,
            "hold_rate": round(held / len(gap_up_days), 2),
        }
    except Exception:
        return {}


# ── Main Agent ────────────────────────────────────────────────────────────────

class PreOpenMonitor(Agent):
    def __init__(self, config: dict):
        super().__init__("PreOpenMonitor", config)
        self.watchlist = config.get("watchlist", [])

    def run(self, context: Optional[dict] = None) -> AgentResult:
        return self._result(self.scan())

    def scan(self) -> dict:
        """
        Full pre-open scan across ALL NIFTY 50 stocks (not just watchlist).
        Any stock with a qualifying gap-up signal gets added to watchlist automatically.
        """
        logger.info("PreOpenMonitor: scanning ALL NIFTY 50 pre-open prices...")

        # Fetch ALL NIFTY 50 from NSE (not just watchlist)
        all_preopen = fetch_preopen_prices()

        # If NSE API failed, fall back to watchlist only
        if not all_preopen:
            logger.warning("NSE pre-open API unavailable, falling back to watchlist")
            all_preopen = _fetch_preopen_yfinance(self.watchlist)

        # Also ensure watchlist stocks are included (in case not in NIFTY 50)
        missing = [s for s in self.watchlist if s not in all_preopen]
        if missing:
            all_preopen.update(_fetch_preopen_yfinance(missing))

        logger.info(f"  {len(all_preopen)} stocks fetched from NSE pre-open")

        # Filter ALL stocks with significant gaps (not just watchlist)
        significant = {
            sym: data for sym, data in all_preopen.items()
            if abs(data.get("gap_pct", 0)) >= GAP_UP_THRESHOLD
        }

        logger.info(f"  {len(significant)} stocks with significant gaps (±{GAP_UP_THRESHOLD}%)")

        # Deep analyze each gap
        analyses = []
        for symbol, gap_data in significant.items():
            analysis = analyze_gap(symbol, gap_data)
            analyses.append(analysis)
            logger.info(
                f"  {symbol}: gap={gap_data['gap_pct']:+.1f}% → {analysis['trade_signal']} "
                f"(conf={analysis['confidence']}%) — {analysis['reasoning'][:60]}"
            )

        # Sort: BUY first, then by confidence
        analyses.sort(key=lambda x: (x["trade_signal"] != "BUY", -x["confidence"]))

        buy_signals  = [a for a in analyses if a["trade_signal"] == "BUY"]
        avoid_signals = [a for a in analyses if a["trade_signal"] == "AVOID"]
        watch_signals = [a for a in analyses if a["trade_signal"] == "WATCH"]

        # Auto-add BUY signal stocks to watchlist
        if buy_signals:
            self._add_to_watchlist([s["symbol"] for s in buy_signals])

        return {
            "scanned_at": datetime.now().isoformat(),
            "total_scanned": len(all_preopen),
            "significant_gaps": len(significant),
            "buy_signals": buy_signals,
            "avoid_signals": avoid_signals,
            "watch_signals": watch_signals,
            "all_preopen": all_preopen,
        }

    def _add_to_watchlist(self, symbols: list[str]) -> None:
        """Append new symbols to the dynamic watchlist file (LOW-10).

        Was previously rewriting ``config.yaml`` and losing comments.
        Now writes to ``data/dynamic_watchlist.json`` instead.
        """
        try:
            from core.watchlist import add_to_dynamic_watchlist
            new = add_to_dynamic_watchlist(symbols)
            if new:
                logger.info(f"Auto-added to dynamic watchlist: {new}")
        except Exception as e:
            logger.error(f"Failed to update dynamic watchlist: {e}")


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = PreOpenMonitor(config)

    SEP = "=" * 60
    print(f"\n{SEP}")
    print("  PRE-OPEN MONITOR")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    result = agent.scan()

    # Show all pre-open prices
    print(f"\n📊 PRE-OPEN PRICES (watchlist):")
    print(f"  {'Symbol':<14} {'Prev Close':>10} {'Pre-Open':>10} {'Gap':>8} {'Volume':>12}")
    print(f"  {'-'*56}")
    for sym, d in sorted(result["all_preopen"].items(), key=lambda x: x[1].get("gap_pct", 0), reverse=True):
        gap = d.get("gap_pct", 0)
        emoji = "🟢" if gap > 1.5 else ("🔴" if gap < -1.5 else "⚪")
        vol = d.get("preopen_volume", 0)
        print(f"  {emoji} {sym:<12} ₹{d.get('prev_close', 0):>9.2f} ₹{d.get('preopen_price', 0):>9.2f} {gap:>+7.2f}% {vol:>12,}")

    # Trade signals
    if result["buy_signals"]:
        print(f"\n🟢 BUY SIGNALS ({len(result['buy_signals'])}):")
        for s in result["buy_signals"]:
            print(f"\n  {s['symbol']} — {s['gap_strength'].upper()} GAP-UP {s['gap_pct']:+.1f}%")
            print(f"  Confidence : {s['confidence']}%")
            print(f"  Entry      : ₹{s['entry']:.2f}")
            print(f"  Stop Loss  : ₹{s['stop_loss']:.2f}")
            print(f"  Target     : ₹{s['target']:.2f}")
            print(f"  Reasoning  : {s['reasoning']}")
            if s["catalysts"]:
                print(f"  Catalysts  :")
                for c in s["catalysts"]:
                    print(f"    ✅ {c}")
            if s["risks"]:
                print(f"  Risks      :")
                for r in s["risks"]:
                    print(f"    ⚠️  {r}")

    if result["avoid_signals"]:
        print(f"\n🔴 AVOID ({len(result['avoid_signals'])}):")
        for s in result["avoid_signals"]:
            print(f"  {s['symbol']}: gap {s['gap_pct']:+.1f}% — {s['reasoning']}")

    if result["watch_signals"]:
        print(f"\n🟡 WATCH ({len(result['watch_signals'])}):")
        for s in result["watch_signals"]:
            print(f"  {s['symbol']}: gap {s['gap_pct']:+.1f}% — {s['reasoning']}")

    if not result["buy_signals"] and not result["avoid_signals"]:
        print(f"\n  No significant gaps today (threshold: ±{GAP_UP_THRESHOLD}%)")

    print(f"\n{SEP}")
