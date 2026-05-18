"""
Filter performance dashboard — Stage 0 measurement scaffolding.

For every filter in the trading pipeline, report on a population of
(symbol, date) decision points:

  trigger_rate  : P(filter fires)
  precision     : P(forward_return > threshold | filter fires)
  recall        : P(filter fires | forward_return > threshold)
  lift          : precision / base_rate  (1.0 = no edge)

Broken down by: overall, by regime, by stock-tier (large / mid / small cap).

Run on EC2 against real stocks/ + paper_trades.db:

    .venv/bin/python scripts/eval_filters.py
    .venv/bin/python scripts/eval_filters.py --horizon 5 --threshold 1.5
    .venv/bin/python scripts/eval_filters.py --output report.md

Run locally with synthetic data (smoke test only — numbers are meaningless):

    .venv/bin/python scripts/eval_filters.py --synthetic

The harness loads price data once, computes all features once, then evaluates
each filter as a vector operation against the same population. This makes it
cheap to add new filters (just register one function).
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sqlite3
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure repo root is on sys.path so this script runs from anywhere.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core import features as F

warnings.filterwarnings("ignore")
logger = logging.getLogger("eval_filters")

REPO = _REPO


# ── Data loading ─────────────────────────────────────────────────────────────

@dataclass
class StockData:
    symbol: str
    df: pd.DataFrame                  # OHLCV indexed by date
    sentiment: Optional[pd.Series] = None    # date -> sentiment if KB present
    pattern_ev: Optional[float] = None       # last computed DTW EV (single scalar)
    pattern_winrate: Optional[float] = None
    market_cap_tier: str = "unknown"   # large / mid / small / unknown


def load_watchlist() -> List[str]:
    """Read watchlist from config.yaml."""
    import yaml
    with open(REPO / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("watchlist", []))


def load_market_caps() -> Dict[str, str]:
    """Best-effort: classify watchlist into large/mid/small from each stock's
    fundamentals.json if present. Falls back to 'large' for NIFTY 50 names.
    """
    from core.symbols import NIFTY_50
    nifty50 = set(NIFTY_50)
    tiers: Dict[str, str] = {}
    for d in (REPO / "stocks").iterdir() if (REPO / "stocks").exists() else []:
        if not d.is_dir():
            continue
        sym = d.name
        fund_path = d / "fundamentals.json"
        if fund_path.exists():
            try:
                fund = json.loads(fund_path.read_text())
                mcap = fund.get("market_cap")
                if mcap:
                    if mcap >= 1e12:                tiers[sym] = "large"
                    elif mcap >= 2e11:              tiers[sym] = "mid"
                    else:                           tiers[sym] = "small"
                    continue
            except Exception:
                pass
        tiers[sym] = "large" if sym in nifty50 else "unknown"
    return tiers


def load_stock_data(symbol: str, mcap_tier: str) -> Optional[StockData]:
    """Load one stock's price history + KB metadata."""
    pq = REPO / "stocks" / symbol / "price_history.parquet"
    if not pq.exists():
        return None
    df = pd.read_parquet(pq).sort_index().dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if len(df) < 200:
        return None
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)

    data = StockData(symbol=symbol, df=df, market_cap_tier=mcap_tier)

    # News sentiment from KB — flat aggregate, not time-resolved (best we have).
    news_path = REPO / "stocks" / symbol / "news_history.json"
    if news_path.exists():
        try:
            news = json.loads(news_path.read_text()).get("news", [])
            # Build a sentiment series indexed by date (mean of headlines that day).
            rows = []
            for n in news:
                if "fetched_at" in n and "sentiment" in n:
                    try:
                        d = pd.to_datetime(n["fetched_at"]).date()
                        rows.append((d, n["sentiment"]))
                    except Exception:
                        pass
            if rows:
                s = pd.DataFrame(rows, columns=["date", "sent"]).groupby("date")["sent"].mean()
                s.index = pd.to_datetime(s.index)
                data.sentiment = s
        except Exception:
            pass

    pat_path = REPO / "stocks" / symbol / "patterns.json"
    if pat_path.exists():
        try:
            pat = json.loads(pat_path.read_text()).get("summary", {})
            data.pattern_ev = pat.get("expected_value")
            data.pattern_winrate = pat.get("win_rate")
        except Exception:
            pass

    return data


def synthesize_stock_data(symbol: str, n_days: int = 750) -> StockData:
    """Produce realistic-ish OHLCV for local self-test only."""
    rng = np.random.default_rng(hash(symbol) % (2**32))
    rets = rng.normal(0.0005, 0.018, n_days)
    close = pd.Series(100 * np.exp(np.cumsum(rets)),
                      index=pd.date_range("2023-01-01", periods=n_days, freq="B"))
    high = close * (1 + rng.uniform(0, 0.012, n_days))
    low = close * (1 - rng.uniform(0, 0.012, n_days))
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.003, n_days))
    vol = pd.Series(rng.integers(1_000_000, 10_000_000, n_days), index=close.index, dtype=float)
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})
    return StockData(symbol=symbol, df=df, market_cap_tier="large")


# ── Feature computation ──────────────────────────────────────────────────────

def compute_features(sd: StockData) -> pd.DataFrame:
    """Compute every feature each filter could need, once per (symbol, date)."""
    df = sd.df
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    feat = pd.DataFrame(index=df.index)
    feat["symbol"] = sd.symbol
    feat["mcap_tier"] = sd.market_cap_tier
    feat["close"] = c

    # Trend
    feat["ema20"] = F.ema(c, 20)
    feat["ema50"] = F.ema(c, 50)
    feat["ema200"] = F.ema(c, 200)
    feat["above_ema20"] = c > feat["ema20"]
    feat["above_ema50"] = c > feat["ema50"]
    feat["above_ema200"] = c > feat["ema200"]
    feat["trend_up"] = (c > feat["ema50"]) & (feat["ema20"] > feat["ema50"])
    feat["trend_down"] = (c < feat["ema50"]) & (feat["ema20"] < feat["ema50"])

    # Momentum
    feat["rsi_14"] = F.rsi(c, 14)
    macd_line, signal_line, hist = F.macd(c)
    feat["macd_bullish"] = macd_line > signal_line
    feat["macd_hist"] = hist

    # Volatility
    feat["atr_14"] = F.atr(h, l, c, 14)
    feat["atr_pct"] = feat["atr_14"] / c * 100

    # Volume
    feat["vol_ratio_20"] = F.volume_ratio(v, 20)

    # ADX → regime proxy
    feat["adx_14"] = F.adx(h, l, c, 14)

    # Daily return + 20-day return
    feat["ret_1d"] = c.pct_change() * 100
    feat["ret_20d"] = (c / c.shift(20) - 1) * 100

    # Per-bar regime classification (matches RegimeAgent rules)
    hvol_20 = c.pct_change().rolling(20).std() * np.sqrt(252) * 100
    feat["hvol_20d"] = hvol_20
    is_bull = (feat["adx_14"] > 25) & (feat["ret_20d"] > 2)
    is_bear = (feat["adx_14"] > 25) & (feat["ret_20d"] < -2)
    is_high_vol = hvol_20 > 20
    feat["regime"] = np.where(is_bull, "trending_bull",
                       np.where(is_bear, "trending_bear",
                       np.where(is_high_vol, "high_volatility", "ranging")))

    # Technical composite (matches TechnicalAgent's 0-10 score, deterministic part)
    score = (
        feat["above_ema20"].astype(int) +
        feat["above_ema50"].astype(int) +
        feat["above_ema200"].astype(int) +
        ((feat["rsi_14"] >= 40) & (feat["rsi_14"] <= 60)).astype(int) +
        feat["macd_bullish"].astype(int) +
        (feat["adx_14"] > 25).astype(int) +
        (feat["atr_pct"] < 2.0).astype(int)
    )  # 7 of the 10 components, the deterministic ones
    feat["tech_score_partial"] = score

    # Sentiment join (if KB had any news)
    if sd.sentiment is not None:
        feat["sentiment"] = sd.sentiment.reindex(feat.index, method="ffill")
    else:
        feat["sentiment"] = np.nan

    # Pattern EV / win rate as constants per symbol (best the KB gives us)
    feat["pattern_ev"] = sd.pattern_ev if sd.pattern_ev is not None else np.nan
    feat["pattern_winrate"] = sd.pattern_winrate if sd.pattern_winrate is not None else np.nan

    return feat


def add_forward_labels(feat: pd.DataFrame, horizon: int, threshold_pct: float) -> pd.DataFrame:
    """Add forward_return_pct and binary label columns."""
    feat = feat.copy()
    feat["fwd_ret"] = (feat["close"].shift(-horizon) / feat["close"] - 1) * 100
    feat["label"] = feat["fwd_ret"] > threshold_pct
    return feat


# ── Filters ──────────────────────────────────────────────────────────────────

@dataclass
class Filter:
    name: str
    predicate: Callable[[pd.DataFrame], pd.Series]
    description: str = ""


FILTERS: List[Filter] = [
    Filter("trend_up",       lambda f: f["trend_up"],
           "EMA-stack uptrend (close>EMA50 and EMA20>EMA50)"),
    Filter("macd_bullish",   lambda f: f["macd_bullish"],
           "MACD line above signal line"),
    Filter("vol_ratio_ge_1", lambda f: f["vol_ratio_20"] >= 1.0,
           "Volume >= 20-day mean volume"),
    Filter("vol_ratio_ge_1_5", lambda f: f["vol_ratio_20"] >= 1.5,
           "Volume >= 1.5x 20-day mean (institutional confirmation)"),
    Filter("rsi_neutral_zone", lambda f: (f["rsi_14"] >= 40) & (f["rsi_14"] <= 60),
           "RSI between 40-60 (no extremes)"),
    Filter("rsi_oversold",    lambda f: f["rsi_14"] < 30,
           "RSI < 30"),
    Filter("rsi_overbought",  lambda f: f["rsi_14"] > 70,
           "RSI > 70"),
    Filter("adx_strong_trend", lambda f: f["adx_14"] > 25,
           "ADX > 25 — directional trend present"),
    Filter("tech_score_ge_5", lambda f: f["tech_score_partial"] >= 5,
           "Deterministic technical score >= 5 (out of 7 partial)"),
    Filter("regime_bull",     lambda f: f["regime"] == "trending_bull",
           "Regime classified as trending_bull"),
    Filter("regime_not_bear", lambda f: f["regime"] != "trending_bear",
           "Regime is NOT trending_bear"),
    Filter("sentiment_positive", lambda f: f["sentiment"] > 0,
           "News sentiment positive (where KB has news)"),
    Filter("sentiment_strong_pos", lambda f: f["sentiment"] > 0.3,
           "News sentiment > 0.3 (strong positive)"),
    Filter("pattern_ev_positive", lambda f: f["pattern_ev"] > 0,
           "DTW pattern EV positive"),
    Filter("pattern_winrate_ge_55", lambda f: f["pattern_winrate"] >= 55,
           "DTW pattern win rate >= 55%"),
    # Composite filter — what the rule-based fallback in master.py actually requires
    Filter("composite_buy_gate",
           lambda f: f["trend_up"] & f["macd_bullish"] & (f["vol_ratio_20"] >= 1.0),
           "Hard gate: trend_up AND MACD_bullish AND vol_ratio>=1.0"),
    # Stage 3a: sentiment quality
    Filter("sentiment_fresh",
           lambda f: f["quality"].str.match("fresh") if "quality" in f.columns else pd.Series(False, index=f.index),
           "Sentiment quality == 'fresh' (Stage 3a)"),
    # Stage 3b: probabilistic regime
    Filter("regime_bull_proba_ge_50",
           lambda f: f.get("regime_bull_proba", pd.Series(0.0, index=f.index)) >= 0.5,
           "P(trending_bull) >= 0.50 (Stage 3b GMM)"),
    # Stage 4: learned tech score
    Filter("learned_tech_proba_ge_55",
           lambda f: f.get("learned_tech_proba", pd.Series(0.0, index=f.index)) >= 0.55,
           "Learned tech score P(fwd>1.5%) >= 0.55 (Stage 4)"),
]


# ML filters are added dynamically only if model pickles exist
def _maybe_add_ml_filters() -> None:
    daily_pkl = REPO / "stocks" / "ml_signal_model.pkl"
    intraday_pkl = REPO / "models" / "stocks_1h" / "india_intraday_model.pkl"
    if daily_pkl.exists():
        logger.info("Daily ML model pickle found; ML filter registration deferred to runtime.")
    if intraday_pkl.exists():
        logger.info("Intraday ML model pickle found; ML filter registration deferred to runtime.")
    # ML filters need feature-pipeline alignment with the trained model; we
    # add a stub so the report shows them as 'not evaluated' rather than
    # silently missing.


# ── Metrics ──────────────────────────────────────────────────────────────────

@dataclass
class FilterMetrics:
    filter_name: str
    n: int = 0
    n_triggered: int = 0
    n_positive: int = 0
    n_triggered_and_positive: int = 0

    @property
    def trigger_rate(self) -> float:
        return self.n_triggered / self.n if self.n else 0.0

    @property
    def precision(self) -> float:
        return self.n_triggered_and_positive / self.n_triggered if self.n_triggered else 0.0

    @property
    def recall(self) -> float:
        return self.n_triggered_and_positive / self.n_positive if self.n_positive else 0.0

    @property
    def base_rate(self) -> float:
        return self.n_positive / self.n if self.n else 0.0

    @property
    def lift(self) -> float:
        return self.precision / self.base_rate if self.base_rate > 0 else 0.0


def evaluate_filter(f: Filter, panel: pd.DataFrame, mask: pd.Series) -> FilterMetrics:
    sub = panel[mask & panel["label"].notna()]
    if sub.empty:
        return FilterMetrics(filter_name=f.name)
    fired = f.predicate(sub).fillna(False)
    label = sub["label"]
    return FilterMetrics(
        filter_name=f.name,
        n=len(sub),
        n_triggered=int(fired.sum()),
        n_positive=int(label.sum()),
        n_triggered_and_positive=int((fired & label).sum()),
    )


# ── Reporting ────────────────────────────────────────────────────────────────

def format_table(rows: List[FilterMetrics], title: str) -> str:
    out = [f"### {title}", ""]
    out.append("| Filter | n | Trigger % | Base rate % | Precision % | Lift | Recall % |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        if r.n == 0:
            out.append(f"| `{r.filter_name}` | 0 | – | – | – | – | – |")
            continue
        out.append(f"| `{r.filter_name}` | {r.n:,} | "
                   f"{r.trigger_rate*100:.1f} | "
                   f"{r.base_rate*100:.1f} | "
                   f"{r.precision*100:.1f} | "
                   f"{r.lift:.2f} | "
                   f"{r.recall*100:.1f} |")
    out.append("")
    return "\n".join(out)


def build_report(panel: pd.DataFrame, horizon: int, threshold_pct: float) -> str:
    out = ["# Filter Performance Report",
           "",
           f"- Horizon: {horizon} bars",
           f"- Label threshold: forward return > {threshold_pct}%",
           f"- Universe: {panel['symbol'].nunique()} symbols, "
           f"{len(panel):,} (symbol, date) decision points",
           f"- Base rate (pre-filter): {panel['label'].mean()*100:.1f}% "
           f"of points have forward return > {threshold_pct}%",
           ""]

    overall = [evaluate_filter(f, panel, pd.Series(True, index=panel.index))
               for f in FILTERS]
    out.append(format_table(overall, "Overall"))

    # By regime
    for regime in ("trending_bull", "ranging", "trending_bear", "high_volatility"):
        mask = panel["regime"] == regime
        if mask.sum() < 100:
            continue
        sub_metrics = [evaluate_filter(f, panel, mask) for f in FILTERS]
        out.append(format_table(sub_metrics, f"By regime: {regime}"))

    # By market-cap tier
    for tier in ("large", "mid", "small"):
        mask = panel["mcap_tier"] == tier
        if mask.sum() < 100:
            continue
        sub_metrics = [evaluate_filter(f, panel, mask) for f in FILTERS]
        out.append(format_table(sub_metrics, f"By market-cap tier: {tier}"))

    return "\n".join(out)


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--horizon", type=int, default=5, help="Forward-return horizon in bars (default: 5)")
    parser.add_argument("--threshold", type=float, default=1.5, help="Forward-return threshold %% (default: 1.5)")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated subset; default = full watchlist")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data (local smoke test only)")
    parser.add_argument("--output", type=str, default="", help="Path to write Markdown report (also printed)")
    parser.add_argument("--json", type=str, default="", help="Path to write JSON summary")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or load_watchlist()
    mcap = load_market_caps()
    panels: List[pd.DataFrame] = []
    loaded = skipped = 0

    for sym in symbols:
        sd = (synthesize_stock_data(sym) if args.synthetic
              else load_stock_data(sym, mcap.get(sym, "unknown")))
        if sd is None:
            skipped += 1
            continue
        feat = compute_features(sd)
        feat = add_forward_labels(feat, horizon=args.horizon, threshold_pct=args.threshold)
        panels.append(feat)
        loaded += 1

    if not panels:
        print(f"ERROR: no usable stock data found. {skipped} symbols skipped.", file=sys.stderr)
        print("       Run on EC2 where stocks/ is populated, or pass --synthetic for a smoke test.",
              file=sys.stderr)
        return 1

    panel = pd.concat(panels, ignore_index=False)
    print(f"# Loaded {loaded} symbols, {len(panel):,} (symbol,date) rows "
          f"({skipped} symbols skipped — no price_history.parquet)", file=sys.stderr)

    _maybe_add_ml_filters()

    report = build_report(panel, horizon=args.horizon, threshold_pct=args.threshold)
    print(report)

    if args.output:
        Path(args.output).write_text(report)
        print(f"# Wrote {args.output}", file=sys.stderr)

    if args.json:
        # JSON summary: overall metrics for each filter
        overall = [evaluate_filter(f, panel, pd.Series(True, index=panel.index)) for f in FILTERS]
        payload = {
            "horizon_bars": args.horizon,
            "threshold_pct": args.threshold,
            "universe_symbols": panel["symbol"].nunique(),
            "rows": int(len(panel)),
            "base_rate": float(panel["label"].mean()),
            "filters": [
                {"name": m.filter_name, "n": m.n, "n_triggered": m.n_triggered,
                 "trigger_rate": m.trigger_rate, "precision": m.precision,
                 "recall": m.recall, "lift": m.lift, "base_rate": m.base_rate}
                for m in overall
            ],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"# Wrote {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
