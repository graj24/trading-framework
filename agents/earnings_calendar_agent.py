"""
Earnings Calendar Agent — the "predict before it moves" engine.

What it does:
1. Fetches upcoming earnings dates for all watchlist stocks
2. Tracks historical earnings reactions per stock (from KB)
3. Monitors NSE/BSE corporate filings overnight for actual results
4. When a result is filed: scores it (beat/miss/inline) and generates a pre-market signal
5. Stores everything in the knowledge base for RAG context

Flow:
  Evening (after 3:30 PM):
    → Which stocks report results tonight/tomorrow?
    → What does history say about their reactions?
    → Set alert for overnight monitoring

  Overnight (every 30 min, 6 PM - 8 AM):
    → Poll NSE corporate filings API
    → New filing detected? Parse it, score it
    → Generate pre-market signal

  Morning (6:00 AM):
    → Summarize all overnight results
    → Flag BUY/AVOID for each
    → Feed into Master Agent context
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, date
from typing import Optional

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from agents.base import Agent, AgentResult
from core.knowledge_base import read_kb, write_kb, kb_path

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
TIMEOUT = 10


# ── NSE Corporate Filings ─────────────────────────────────────────────────────

def _nse_session() -> requests.Session:
    """Create a session with NSE cookies."""
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
    return s


def fetch_nse_corporate_filings(symbol: str, days_back: int = 1) -> list[dict]:
    """
    Fetch recent corporate filings from NSE for a symbol.
    Returns list of filings with subject, date, attachment URL.
    """
    filings = []
    try:
        s = _nse_session()
        url = f"https://www.nseindia.com/api/corp-announcements?index=equities&symbol={symbol}"
        resp = s.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        cutoff = datetime.now() - timedelta(days=days_back)

        for item in (data if isinstance(data, list) else []):
            try:
                dt_str = item.get("excDt") or item.get("brdMtngDt") or ""
                dt = datetime.strptime(dt_str[:10], "%d-%b-%Y") if dt_str else datetime.now()
            except Exception:
                dt = datetime.now()

            if dt >= cutoff:
                filings.append({
                    "symbol": symbol,
                    "subject": item.get("subject", item.get("desc", "")),
                    "date": dt.strftime("%Y-%m-%d"),
                    "attachment": item.get("attchmntFile", ""),
                    "raw": item,
                })
    except Exception as e:
        logger.debug(f"NSE filings failed for {symbol}: {e}")
    return filings


def fetch_bse_results(symbol: str) -> list[dict]:
    """
    Fetch quarterly results from BSE corporate announcements.
    BSE is often faster than NSE for result filings.
    """
    filings = []
    try:
        # BSE scrip code lookup via yfinance info
        t = yf.Ticker(symbol + ".NS")
        info = t.info or {}
        # Try BSE API with company name search
        company = info.get("longName", symbol)
        url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=Result&strPrevDate=&strScrip=&strSearch=P&strToDate=&strType=C&subcategory=-1"
        resp = requests.get(url, headers={**HEADERS, "Referer": "https://www.bseindia.com/"}, timeout=TIMEOUT)
        data = resp.json()
        for item in (data.get("Table", []) or [])[:20]:
            headline = item.get("HEADLINE", "")
            if symbol.upper() in headline.upper() or company.split()[0].upper() in headline.upper():
                filings.append({
                    "symbol": symbol,
                    "subject": headline,
                    "date": item.get("NEWS_DT", "")[:10],
                    "source": "bse",
                })
    except Exception as e:
        logger.debug(f"BSE results failed for {symbol}: {e}")
    return filings


def fetch_earnings_calendar_yf(symbol: str) -> Optional[str]:
    """Get next earnings date from yfinance."""
    try:
        t = yf.Ticker(symbol + ".NS")
        cal = t.calendar
        if cal is not None and not cal.empty:
            # calendar is a DataFrame with dates as columns
            cols = cal.columns.tolist()
            if cols:
                return str(cols[0].date()) if hasattr(cols[0], 'date') else str(cols[0])
    except Exception:
        pass
    return None


# ── B9: Yahoo Finance EPS consensus ──────────────────────────────────────────

def fetch_eps_consensus(symbol: str) -> dict:
    """Scrape EPS estimate vs actual from Yahoo Finance analysis page.

    Returns:
        {
          "symbol": str,
          "eps_estimate": float | None,   # analyst consensus EPS estimate
          "eps_actual":   float | None,   # reported EPS (most recent quarter)
          "beat_pct":     float | None,   # (actual - estimate) / |estimate| * 100
          "verdict":      "BEAT"|"MISS"|"INLINE"|"UNKNOWN",
          "source":       "yahoo_finance",
        }
    """
    result: dict = {
        "symbol": symbol,
        "eps_estimate": None,
        "eps_actual": None,
        "beat_pct": None,
        "verdict": "UNKNOWN",
        "source": "yahoo_finance",
    }
    try:
        t = yf.Ticker(symbol + ".NS")

        # yfinance exposes earnings history directly
        eh = t.earnings_history
        if eh is not None and not eh.empty:
            # Most recent row
            row = eh.iloc[-1]
            est = row.get("epsEstimate") if hasattr(row, "get") else row["epsEstimate"]
            act = row.get("epsActual")   if hasattr(row, "get") else row["epsActual"]
            if est is not None and act is not None:
                try:
                    est_f = float(est)
                    act_f = float(act)
                    result["eps_estimate"] = round(est_f, 4)
                    result["eps_actual"]   = round(act_f, 4)
                    if abs(est_f) > 1e-6:
                        beat_pct = (act_f - est_f) / abs(est_f) * 100
                        result["beat_pct"] = round(beat_pct, 2)
                        if beat_pct > 5:
                            result["verdict"] = "BEAT"
                        elif beat_pct < -5:
                            result["verdict"] = "MISS"
                        else:
                            result["verdict"] = "INLINE"
                except (TypeError, ValueError):
                    pass

        # Fallback: try analyst_price_targets / info for forward EPS
        if result["verdict"] == "UNKNOWN":
            info = t.info or {}
            fwd_eps = info.get("forwardEps")
            trail_eps = info.get("trailingEps")
            if fwd_eps and trail_eps:
                result["eps_estimate"] = round(float(fwd_eps), 4)
                result["eps_actual"]   = round(float(trail_eps), 4)
                # Can't compute beat/miss without quarterly granularity
                result["verdict"] = "UNKNOWN"

    except Exception as e:
        logger.debug("fetch_eps_consensus(%s) failed: %s", symbol, e)

    return result


# ── Result Scoring ────────────────────────────────────────────────────────────

BEAT_KEYWORDS = {
    "beat", "beats", "exceeds", "surpasses", "above estimate", "above expectation",
    "record", "highest ever", "strong", "robust", "outperform", "profit up",
    "revenue up", "growth", "expansion", "positive", "better than expected",
}
MISS_KEYWORDS = {
    "miss", "misses", "below estimate", "below expectation", "disappoints",
    "disappointing", "weak", "decline", "fall", "drop", "loss", "lower than",
    "worse than expected", "profit down", "revenue down",
}
INLINE_KEYWORDS = {"inline", "in line", "meets", "as expected", "flat", "stable"}


def score_result(subject: str, content: str = "", eps_consensus: dict | None = None) -> dict:
    """
    Score an earnings result filing as BEAT / MISS / INLINE.

    B9: when ``eps_consensus`` is provided (from fetch_eps_consensus), the
    quantitative EPS beat/miss overrides the keyword heuristic and raises
    confidence significantly.

    Returns: {verdict, confidence, signal, reasoning}
    """
    text = (subject + " " + content).lower()

    beat_count = sum(1 for kw in BEAT_KEYWORDS if kw in text)
    miss_count = sum(1 for kw in MISS_KEYWORDS if kw in text)
    inline_count = sum(1 for kw in INLINE_KEYWORDS if kw in text)

    total = beat_count + miss_count + inline_count or 1

    # Keyword-based baseline
    if beat_count > miss_count and beat_count > inline_count:
        verdict, confidence, signal = "BEAT", min(0.75, beat_count / total), "BUY"
    elif miss_count > beat_count and miss_count > inline_count:
        verdict, confidence, signal = "MISS", min(0.75, miss_count / total), "AVOID"
    elif inline_count > 0:
        verdict, confidence, signal = "INLINE", 0.5, "NEUTRAL"
    else:
        verdict, confidence, signal = "UNKNOWN", 0.3, "NEUTRAL"

    # B9: override with quantitative EPS data when available
    eps_note = ""
    if eps_consensus and eps_consensus.get("verdict") not in (None, "UNKNOWN"):
        q_verdict = eps_consensus["verdict"]
        beat_pct  = eps_consensus.get("beat_pct", 0) or 0
        est       = eps_consensus.get("eps_estimate")
        act       = eps_consensus.get("eps_actual")
        verdict   = q_verdict
        signal    = "BUY" if q_verdict == "BEAT" else ("AVOID" if q_verdict == "MISS" else "NEUTRAL")
        # Higher confidence for larger beats/misses
        confidence = min(0.95, 0.65 + abs(beat_pct) / 100)
        eps_note = f" | EPS est={est} act={act} ({beat_pct:+.1f}%)"

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "signal": signal,
        "beat_signals": beat_count,
        "miss_signals": miss_count,
        "reasoning": f"{verdict}: {beat_count} positive / {miss_count} negative keywords{eps_note}",
    }


# ── Historical Reaction Analysis ──────────────────────────────────────────────

def compute_historical_earnings_reaction(symbol: str) -> dict:
    """
    Compute how this stock historically reacts to earnings.
    Uses price history + earnings dates from KB.
    """
    path = kb_path(symbol) / "price_history.parquet"
    earnings_kb = read_kb(symbol, "earnings_history.json")

    if not path.exists():
        return {}

    import pandas as pd
    price_df = pd.read_parquet(path).sort_index()
    price_df.index = pd.to_datetime(price_df.index, utc=True).tz_localize(None)

    reactions = []
    for q in earnings_kb.get("quarters", []):
        try:
            dt = pd.Timestamp(q["date"])
            idx = price_df.index.searchsorted(dt)
            if 0 < idx < len(price_df) - 3:
                # Day-of reaction
                day_of = (price_df.iloc[idx]["Close"] - price_df.iloc[idx-1]["Close"]) / price_df.iloc[idx-1]["Close"] * 100
                # 3-day reaction
                three_day = (price_df.iloc[idx+2]["Close"] - price_df.iloc[idx-1]["Close"]) / price_df.iloc[idx-1]["Close"] * 100
                reactions.append({
                    "date": q["date"],
                    "day_of_pct": round(day_of, 2),
                    "three_day_pct": round(three_day, 2),
                })
        except Exception:
            continue

    if not reactions:
        return {"reactions": [], "avg_day_of": 0, "avg_three_day": 0, "beat_avg": 0, "miss_avg": 0}

    day_of_vals = [r["day_of_pct"] for r in reactions]
    three_day_vals = [r["three_day_pct"] for r in reactions]

    return {
        "reactions": reactions,
        "avg_day_of_pct": round(sum(day_of_vals) / len(day_of_vals), 2),
        "avg_three_day_pct": round(sum(three_day_vals) / len(three_day_vals), 2),
        "best_reaction": max(day_of_vals),
        "worst_reaction": min(day_of_vals),
        "positive_count": sum(1 for v in day_of_vals if v > 0),
        "total_count": len(day_of_vals),
    }


# ── Pre-Market Signal Generation ──────────────────────────────────────────────

def generate_premarket_signal(symbol: str, result_score: dict, historical: dict) -> dict:
    """
    Combine result verdict + historical reaction to generate a pre-market signal.

    Logic:
    - BEAT + historically reacts positively → STRONG BUY
    - BEAT + historically mixed → BUY with caution
    - MISS + historically reacts negatively → STRONG AVOID
    - INLINE → NEUTRAL, watch for price action
    """
    verdict = result_score.get("verdict", "UNKNOWN")
    hist_avg = historical.get("avg_day_of_pct", 0)
    hist_positive_rate = historical.get("positive_count", 0) / max(historical.get("total_count", 1), 1)

    if verdict == "BEAT":
        if hist_avg > 2 and hist_positive_rate > 0.6:
            action = "STRONG_BUY"
            confidence = 85
            reasoning = f"Earnings beat + historically reacts +{hist_avg:.1f}% on beats ({hist_positive_rate:.0%} positive rate)"
        elif hist_avg > 0:
            action = "BUY"
            confidence = 65
            reasoning = f"Earnings beat + moderate historical reaction ({hist_avg:+.1f}% avg)"
        else:
            action = "WATCH"
            confidence = 50
            reasoning = f"Earnings beat but historically muted reaction ({hist_avg:+.1f}% avg) — wait for price confirmation"

    elif verdict == "MISS":
        if hist_avg < -2:
            action = "STRONG_AVOID"
            confidence = 85
            reasoning = f"Earnings miss + historically drops {hist_avg:.1f}% on misses"
        else:
            action = "AVOID"
            confidence = 65
            reasoning = f"Earnings miss — avoid until price stabilizes"

    elif verdict == "INLINE":
        action = "NEUTRAL"
        confidence = 50
        reasoning = "Inline results — no directional edge, watch for price action at open"

    else:
        action = "WATCH"
        confidence = 40
        reasoning = "Result filed but verdict unclear — monitor pre-open price"

    return {
        "symbol": symbol,
        "action": action,
        "confidence": confidence,
        "reasoning": reasoning,
        "verdict": verdict,
        "historical_avg_reaction": hist_avg,
        "generated_at": datetime.now().isoformat(),
    }


# ── Main Agent ────────────────────────────────────────────────────────────────

class EarningsCalendarAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("EarningsCalendarAgent", config)
        self.watchlist = config.get("watchlist", [])

    def run(self, context: Optional[dict] = None) -> AgentResult:
        mode = (context or {}).get("mode", "morning_scan")
        if mode == "evening_prep":
            return self._result(self.evening_prep())
        elif mode == "overnight_monitor":
            return self._result(self.overnight_monitor())
        else:
            return self._result(self.morning_scan())

    def evening_prep(self) -> dict:
        """
        Run at 3:30 PM — identify which stocks report results tonight/tomorrow.
        """
        logger.info("EarningsCalendar: evening prep — checking upcoming results...")
        upcoming = []

        for symbol in self.watchlist:
            next_date = fetch_earnings_calendar_yf(symbol)
            historical = compute_historical_earnings_reaction(symbol)

            entry = {
                "symbol": symbol,
                "next_earnings_date": next_date,
                "historical_avg_reaction": historical.get("avg_day_of_pct", 0),
                "historical_positive_rate": historical.get("positive_count", 0) / max(historical.get("total_count", 1), 1),
                "watch": False,
            }

            # Flag if earnings within next 3 days
            if next_date:
                try:
                    days_away = (datetime.strptime(next_date, "%Y-%m-%d").date() - date.today()).days
                    if 0 <= days_away <= 3:
                        entry["watch"] = True
                        entry["days_away"] = days_away
                        logger.info(f"  ⚠️  {symbol}: earnings in {days_away} day(s) — avg reaction {historical.get('avg_day_of_pct', 0):+.1f}%")
                except Exception:
                    pass

            upcoming.append(entry)

        # Save to KB
        for entry in upcoming:
            sym = entry["symbol"]
            existing = read_kb(sym, "earnings_history.json")
            existing["next_earnings_date"] = entry["next_earnings_date"]
            existing["watch_flag"] = entry["watch"]
            write_kb(sym, "earnings_history.json", existing)

        watching = [e for e in upcoming if e.get("watch")]
        logger.info(f"EarningsCalendar: {len(watching)} stocks to watch tonight")
        return {"upcoming": upcoming, "watching": watching}

    def overnight_monitor(self) -> dict:
        """
        Poll NSE/BSE filings every 30 min overnight.
        Returns pre-market signals for any results filed.
        """
        logger.info("EarningsCalendar: overnight monitor — checking filings...")
        signals = []

        for symbol in self.watchlist:
            # Check NSE filings from last 12 hours
            filings = fetch_nse_corporate_filings(symbol, days_back=1)
            filings += fetch_bse_results(symbol)

            for filing in filings:
                subject = filing.get("subject", "")
                # Only process result-related filings
                result_keywords = {"result", "financial", "quarterly", "q1", "q2", "q3", "q4",
                                   "revenue", "profit", "earnings", "annual"}
                if not any(kw in subject.lower() for kw in result_keywords):
                    continue

                logger.info(f"  📄 {symbol}: filing detected — '{subject[:60]}'")

                # Score the result — augment with quantitative EPS data (B9)
                eps_consensus = fetch_eps_consensus(symbol)
                result_score = score_result(subject, eps_consensus=eps_consensus)

                # Get historical reaction
                historical = compute_historical_earnings_reaction(symbol)

                # Generate pre-market signal
                signal = generate_premarket_signal(symbol, result_score, historical)
                signal["filing_subject"] = subject
                signal["filing_date"] = filing.get("date", "")
                signals.append(signal)

                # Save signal to KB
                existing = read_kb(symbol, "event_reactions.json")
                existing.setdefault("premarket_signals", [])
                existing["premarket_signals"].append(signal)
                existing["premarket_signals"] = existing["premarket_signals"][-20:]  # keep last 20
                write_kb(symbol, "event_reactions.json", existing)

                logger.info(f"  → Signal: {signal['action']} (conf={signal['confidence']}%) — {signal['reasoning']}")

        return {"signals": signals, "checked_at": datetime.now().isoformat()}

    def morning_scan(self) -> dict:
        """
        Run at 6:00 AM — summarize all overnight results and flag opportunities.
        """
        logger.info("EarningsCalendar: morning scan...")
        all_signals = []

        for symbol in self.watchlist:
            event_data = read_kb(symbol, "event_reactions.json")
            signals = event_data.get("premarket_signals", [])

            # Only signals from last 12 hours
            cutoff = datetime.now() - timedelta(hours=12)
            recent = [
                s for s in signals
                if datetime.fromisoformat(s.get("generated_at", "2000-01-01")) > cutoff
            ]
            all_signals.extend(recent)

        # Sort by confidence
        all_signals.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        strong_buys = [s for s in all_signals if s["action"] == "STRONG_BUY"]
        buys = [s for s in all_signals if s["action"] == "BUY"]
        avoids = [s for s in all_signals if "AVOID" in s["action"]]

        logger.info(f"Morning scan: {len(strong_buys)} STRONG BUY, {len(buys)} BUY, {len(avoids)} AVOID")
        for s in strong_buys + buys:
            logger.info(f"  🟢 {s['symbol']}: {s['action']} — {s['reasoning']}")
        for s in avoids:
            logger.info(f"  🔴 {s['symbol']}: {s['action']} — {s['reasoning']}")

        return {
            "strong_buys": strong_buys,
            "buys": buys,
            "avoids": avoids,
            "all_signals": all_signals,
            "scanned_at": datetime.now().isoformat(),
        }

    def get_signal_for_stock(self, symbol: str) -> Optional[dict]:
        """Get the latest pre-market signal for a stock (used by Master Agent)."""
        event_data = read_kb(symbol, "event_reactions.json")
        signals = event_data.get("premarket_signals", [])
        if not signals:
            return None
        # Return most recent
        return sorted(signals, key=lambda x: x.get("generated_at", ""), reverse=True)[0]


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = EarningsCalendarAgent(config)

    SEP = "=" * 60

    print(f"\n{SEP}")
    print("  EARNINGS CALENDAR AGENT — FULL REPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    # 1. Evening prep — upcoming earnings
    print("\n📅 UPCOMING EARNINGS (next 3 days):")
    prep = agent.evening_prep()
    watching = prep.get("watching", [])
    if watching:
        for w in watching:
            print(f"  ⚠️  {w['symbol']}: earnings in {w.get('days_away', '?')} day(s)")
            print(f"      Historical avg reaction: {w['historical_avg_reaction']:+.1f}%")
            print(f"      Positive rate: {w['historical_positive_rate']:.0%}")
    else:
        print("  No earnings in next 3 days for watchlist stocks")

    # 2. Historical reactions for all watchlist stocks
    print(f"\n📊 HISTORICAL EARNINGS REACTIONS:")
    for symbol in config["watchlist"]:
        hist = compute_historical_earnings_reaction(symbol)
        if hist.get("total_count", 0) > 0:
            print(f"  {symbol:15s}: avg {hist['avg_day_of_pct']:+.1f}% day-of | "
                  f"best {hist['best_reaction']:+.1f}% | worst {hist['worst_reaction']:+.1f}% | "
                  f"{hist['positive_count']}/{hist['total_count']} positive")
        else:
            print(f"  {symbol:15s}: no reaction data yet")

    # 3. Overnight monitor — check for any recent filings
    print(f"\n🌙 OVERNIGHT FILINGS (last 24h):")
    overnight = agent.overnight_monitor()
    signals = overnight.get("signals", [])
    if signals:
        for s in signals:
            emoji = "🟢" if "BUY" in s["action"] else ("🔴" if "AVOID" in s["action"] else "🟡")
            print(f"  {emoji} {s['symbol']}: {s['action']} (conf={s['confidence']}%)")
            print(f"     Filing: {s.get('filing_subject', '')[:60]}")
            print(f"     Signal: {s['reasoning']}")
    else:
        print("  No result filings detected in last 24 hours")

    # 4. Morning scan summary
    print(f"\n🌅 MORNING SCAN SUMMARY:")
    morning = agent.morning_scan()
    if morning["strong_buys"] or morning["buys"]:
        print("  BUY SIGNALS:")
        for s in morning["strong_buys"] + morning["buys"]:
            print(f"    🟢 {s['symbol']}: {s['action']} — {s['reasoning']}")
    if morning["avoids"]:
        print("  AVOID:")
        for s in morning["avoids"]:
            print(f"    🔴 {s['symbol']}: {s['action']} — {s['reasoning']}")
    if not morning["all_signals"]:
        print("  No overnight signals")

    print(f"\n{SEP}")
