"""
Stage 2C: walk-forward backtest of the 6 intraday pattern detectors.

The scanner used to publish `confidence: 75` for every bull-flag detection —
a literal made-up number. This script replaces that by walking each detector
across historical intraday candle data and computing the *empirical* hit rate
at multiple horizons, sliced by regime and time-of-day.

Output: `models/intraday_detector_stats.json`. The scanner reads this file
at runtime (via `agents.intraday_scanner._load_empirical_stats`) and uses
the empirical hit rate as the pattern's `confidence` field.

Usage on EC2 (where stocks_1h/ has real 1h candles):

    .venv/bin/python scripts/backtest_intraday_detectors.py

Usage locally with synthesised data (smoke test, not predictive):

    .venv/bin/python scripts/backtest_intraday_detectors.py --synthetic
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agents.intraday_scanner import (
    detect_bull_flag, detect_vwap_reclaim, detect_accumulation_at_support,
    detect_volume_spike, detect_resistance_breakout, detect_rsi_divergence,
    _compute_atr_pct,
)
from core import features as F

logger = logging.getLogger("backtest_detectors")

# Horizon (in bars) at which we measure forward returns. For 5-minute data:
#   6 bars  = 30 min
#   12 bars = 1 hour
#   36 bars = 3 hours
HORIZONS_BARS = {"30min": 6, "1h": 12, "3h": 36}

# Define what counts as a successful trade — forward return after horizon
# is positive. Configurable threshold per horizon if needed.
WIN_THRESHOLD_PCT = 0.0


# ── Detector registry ────────────────────────────────────────────────────────

def _detectors():
    """Pairs of (name, fn). Each fn takes (df, atr_pct, quote)."""
    def wrap(fn, needs_quote: bool = False):
        def _call(df, atr_pct, quote):
            if needs_quote:
                return fn(df, quote, atr_pct=atr_pct)
            return fn(df, atr_pct=atr_pct)
        return _call
    return {
        "bull_flag":               wrap(detect_bull_flag),
        "vwap_reclaim":            wrap(detect_vwap_reclaim, needs_quote=True),
        "accumulation_at_support": wrap(detect_accumulation_at_support),
        "volume_spike":            wrap(detect_volume_spike, needs_quote=True),
        "resistance_breakout":     wrap(detect_resistance_breakout),
        "rsi_divergence":          wrap(detect_rsi_divergence),
    }


# ── Regime classification (per-bar, intraday) ────────────────────────────────

def _classify_regime_intraday(df: pd.DataFrame, idx: int,
                              lookback: int = 60) -> str:
    """Lightweight regime label for a single bar. Mirrors RegimeAgent rules
    but applied to a rolling intraday window."""
    if idx < lookback:
        return "unknown"
    window = df.iloc[idx - lookback: idx]
    high, low, close = window["High"], window["Low"], window["Close"]
    adx = F.adx_value(high, low, close, 14)
    ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
    vol = close.pct_change().std() * np.sqrt(252) * 100   # annualised
    if adx > 25 and ret > 0.5:
        return "trending_bull"
    if adx > 25 and ret < -0.5:
        return "trending_bear"
    if vol > 25:
        return "high_volatility"
    return "ranging"


# ── Walk-forward simulation ──────────────────────────────────────────────────

def _quote_from_df(df: pd.DataFrame, idx: int) -> dict:
    """Build the minimal `quote` dict used by detectors that need VWAP/LTP."""
    sub = df.iloc[: idx + 1]
    if len(sub) < 5:
        return {"vwap": float(sub["Close"].iloc[-1]), "ltp": float(sub["Close"].iloc[-1])}
    return {
        "vwap": F.vwap(sub["High"].tail(20), sub["Low"].tail(20),
                        sub["Close"].tail(20), sub["Volume"].tail(20)),
        "ltp": float(sub["Close"].iloc[-1]),
    }


def backtest_one_symbol(df: pd.DataFrame, symbol: str,
                        warmup: int = 60, step: int = 1,
                        cooldown_bars: int = 6) -> list[dict]:
    """For each bar i ≥ warmup, run every detector. Record hit/miss outcomes
    at each horizon. Returns one row per fired pattern."""
    detectors = _detectors()
    rows: list[dict] = []

    closes = df["Close"].values
    n = len(df)
    last_fire: dict[str, int] = {}  # detector_name → last firing bar idx

    for i in range(warmup, n - max(HORIZONS_BARS.values()), step):
        sub = df.iloc[: i + 1]
        atr_pct = _compute_atr_pct(sub)
        quote = _quote_from_df(df, i)
        regime = _classify_regime_intraday(df, i)
        hour = df.index[i].hour if hasattr(df.index[i], "hour") else None

        for name, fn in detectors.items():
            # Cooldown — don't double-count consecutive bar firings.
            if name in last_fire and (i - last_fire[name]) < cooldown_bars:
                continue
            try:
                r = fn(sub, atr_pct, quote)
            except Exception:
                r = None
            if r is None:
                continue
            last_fire[name] = i

            # Forward returns at each horizon.
            entry_price = float(closes[i])
            outcomes = {}
            for h_label, h_bars in HORIZONS_BARS.items():
                if i + h_bars >= n:
                    continue
                fwd = (float(closes[i + h_bars]) / entry_price - 1) * 100
                outcomes[h_label] = fwd

            rows.append({
                "symbol": symbol,
                "bar_idx": i,
                "ts": str(df.index[i]),
                "detector": name,
                "regime": regime,
                "hour": hour,
                "atr_pct": round(atr_pct, 3),
                **{f"fwd_{k}_pct": round(v, 3) for k, v in outcomes.items()},
                **{f"win_{k}": v > WIN_THRESHOLD_PCT for k, v in outcomes.items()},
            })

    return rows


# ── Aggregation ──────────────────────────────────────────────────────────────

def aggregate_stats(rows: list[dict],
                    primary_horizon: str = "1h") -> dict:
    """Roll up to per-detector, per-regime, per-hour hit rates.

    The JSON shape produced is:
        {
          "<detector_name>": {
            "overall":          0.62,
            "<regime>":         0.71,
            "<regime>_<hour>":  0.78,
            "n":                123,
            ...
          }, ...
        }

    `primary_horizon` is the one whose hit rate becomes the "confidence"
    number. 1h matches the existing intraday ML model's horizon.
    """
    win_col = f"win_{primary_horizon}"
    fwd_col = f"fwd_{primary_horizon}_pct"

    df = pd.DataFrame(rows)
    out: dict = {}

    if df.empty:
        return out

    df = df.dropna(subset=[win_col])
    detectors = df["detector"].unique()

    for det in detectors:
        sub = df[df["detector"] == det]
        if sub.empty:
            continue
        det_stats = {
            "n":                  int(len(sub)),
            "overall":            float(sub[win_col].mean()),
            "mean_fwd_pct":       float(sub[fwd_col].mean()),
            "horizon":            primary_horizon,
        }
        # By regime
        for regime, regime_sub in sub.groupby("regime"):
            if len(regime_sub) >= 5:
                det_stats[regime] = float(regime_sub[win_col].mean())
        # By regime × hour
        for (regime, hour), rh_sub in sub.groupby(["regime", "hour"]):
            if len(rh_sub) >= 5 and pd.notna(hour):
                det_stats[f"{regime}_{int(hour)}"] = float(rh_sub[win_col].mean())
        out[det] = det_stats

    return out


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_real_data() -> dict[str, pd.DataFrame]:
    """Load the 1h candle parquets that `india_intraday_model.fetch_all` writes
    to `models/stocks_1h/`. Returns {symbol: dataframe}."""
    data_dir = REPO / "models" / "stocks_1h"
    if not data_dir.exists():
        return {}
    out: dict[str, pd.DataFrame] = {}
    for p in data_dir.glob("*.parquet"):
        if any(skip in p.stem for skip in ["NIFTY", "BANKNIFTY", "VIX", "model"]):
            continue
        try:
            df = pd.read_parquet(p)
            if df.index.tz is not None:
                df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            if len(df) >= 200:
                out[p.stem] = df
        except Exception as e:
            logger.warning(f"skip {p.name}: {e}")
    return out


def synthesize_data(n_symbols: int = 5, n_bars: int = 1500) -> dict[str, pd.DataFrame]:
    """Synthetic 1h-like OHLCV for local self-test."""
    out: dict[str, pd.DataFrame] = {}
    for sym in [f"SYM{i}" for i in range(n_symbols)]:
        rng = np.random.default_rng(hash(sym) % (2**32))
        rets = rng.normal(0.0001, 0.005, n_bars)
        close = pd.Series(100 * np.exp(np.cumsum(rets)),
                          index=pd.date_range("2025-01-01 09:00", periods=n_bars, freq="h"))
        high = close * (1 + rng.uniform(0, 0.005, n_bars))
        low = close * (1 - rng.uniform(0, 0.005, n_bars))
        open_ = close.shift(1).fillna(close.iloc[0])
        vol = pd.Series(rng.integers(50_000, 500_000, n_bars).astype(float),
                        index=close.index)
        out[sym] = pd.DataFrame({"Open": open_, "High": high, "Low": low,
                                  "Close": close, "Volume": vol})
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--synthetic", action="store_true",
                        help="Use synthetic data (local smoke test)")
    parser.add_argument("--horizon", default="1h", choices=list(HORIZONS_BARS.keys()),
                        help="Primary horizon for hit-rate aggregation")
    parser.add_argument("--output", default=None,
                        help="Path for JSON output (default: models/intraday_detector_stats.json)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    data = synthesize_data() if args.synthetic else load_real_data()
    if not data:
        print("ERROR: no symbol data found. Run on EC2 where models/stocks_1h/ "
              "is populated, or pass --synthetic.", file=sys.stderr)
        return 1

    print(f"# Backtesting {len(data)} symbols (horizon={args.horizon})", file=sys.stderr)
    all_rows: list[dict] = []
    for sym, df in data.items():
        rows = backtest_one_symbol(df, sym)
        all_rows.extend(rows)
        if args.verbose:
            print(f"  {sym}: {len(rows)} firings", file=sys.stderr)

    print(f"# Total firings: {len(all_rows):,}", file=sys.stderr)

    stats = aggregate_stats(all_rows, primary_horizon=args.horizon)
    out_path = Path(args.output) if args.output else REPO / "models" / "intraday_detector_stats.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(stats, indent=2))
    print(f"# Wrote {out_path}", file=sys.stderr)

    # Print summary table to stdout.
    print()
    print(f"## Empirical hit rates (horizon = {args.horizon})")
    print()
    print(f"| Detector | n | Overall | Bull | Bear | Ranging | High-Vol |")
    print(f"|---|---:|---:|---:|---:|---:|---:|")
    for det, s in sorted(stats.items()):
        def cell(key):
            v = s.get(key)
            return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "–"
        print(f"| `{det}` | {s.get('n','–')} | {cell('overall')} | "
              f"{cell('trending_bull')} | {cell('trending_bear')} | "
              f"{cell('ranging')} | {cell('high_volatility')} |")

    return 0


if __name__ == "__main__":
    sys.exit(main())
