from __future__ import annotations
from __future__ import annotations
"""Market Regime Detection Agent — bull/bear/ranging/volatile classification."""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from agents.base import Agent, AgentResult
from core import features as F

logger = logging.getLogger(__name__)

STRATEGY_ADJUSTMENTS = {
    'trending_bull': {'position_size_multiplier': 1.2, 'prefer': 'breakouts', 'avoid': 'mean_reversion'},
    'trending_bear': {'position_size_multiplier': 0.5, 'prefer': 'short_or_cash', 'avoid': 'longs'},
    'high_volatility': {'position_size_multiplier': 0.5, 'prefer': 'tight_stops', 'avoid': 'overnight'},
    'ranging': {'position_size_multiplier': 0.8, 'prefer': 'mean_reversion', 'avoid': 'breakouts'},
}


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Wrapper around core.features.adx_value (Stage 0 canonical indicators)."""
    return F.adx_value(high, low, close, period)


class RegimeAgent(Agent):
    def __init__(self, config: dict | None = None):
        super().__init__("RegimeAgent", config or {})

    def run(self, context: dict | None = None) -> AgentResult:
        try:
            df = yf.download("^NSEI", period="120d", progress=False)
            if df.empty or len(df) < 60:
                return self._error("Insufficient Nifty data")
            df = df.tail(60).copy()

            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            high, low, close = df['High'], df['Low'], df['Close']
            adx = compute_adx(high, low, close, 14)

            daily_returns = close.pct_change().dropna()
            ret_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100)
            volatility = float(daily_returns.tail(20).std() * np.sqrt(252) * 100)

            # Fetch India VIX
            vix_val = None
            try:
                vix_df = yf.download("^INDIAVIX", period="5d", progress=False)
                if not vix_df.empty:
                    if isinstance(vix_df.columns, pd.MultiIndex):
                        vix_df.columns = vix_df.columns.get_level_values(0)
                    vix_val = float(vix_df['Close'].iloc[-1])
            except Exception:
                pass

            # Regime classification
            if adx > 25 and ret_20d > 2:
                regime = 'trending_bull'
            elif adx > 25 and ret_20d < -2:
                regime = 'trending_bear'
            elif volatility > 20:
                regime = 'high_volatility'
            else:
                regime = 'ranging'

            # VIX confirmation for high_volatility
            if vix_val and vix_val > 20 and regime == 'ranging':
                regime = 'high_volatility'

            # Confidence calculation
            confidence = 0.5
            if regime == 'trending_bull':
                confidence = min(1.0, 0.5 + (adx - 25) / 50 + ret_20d / 20)
            elif regime == 'trending_bear':
                confidence = min(1.0, 0.5 + (adx - 25) / 50 + abs(ret_20d) / 20)
            elif regime == 'high_volatility':
                confidence = min(1.0, 0.5 + (volatility - 20) / 40)
                if vix_val and vix_val > 20:
                    confidence = min(1.0, confidence + 0.15)
            else:  # ranging
                confidence = min(1.0, 0.5 + (25 - adx) / 50)
            confidence = max(0.0, confidence)

            strategy_adjustments = STRATEGY_ADJUSTMENTS[regime]

            # Stage 3b: probabilistic regime via GaussianMixture.
            regime_proba: dict = {}
            try:
                from models.regime_model import predict_proba as _gmm_proba
                proba = _gmm_proba(ret_20d=ret_20d, vol_20d=volatility,
                                    vix=vix_val if vix_val else 16.0)
                if proba is not None:
                    regime_proba = proba
                    regime = max(proba, key=proba.get)
                    confidence = round(proba[regime], 3)
            except Exception:
                pass

            return self._result({
                'regime': regime,
                'confidence': round(confidence, 3),
                'adx': round(adx, 2),
                'volatility': round(volatility, 2),
                'return_20d': round(ret_20d, 2),
                'india_vix': round(vix_val, 2) if vix_val else None,
                'strategy_adjustments': strategy_adjustments,
                'regime_proba': regime_proba,
            })
        except Exception as e:
            logger.exception("RegimeAgent failed")
            return self._error(str(e))


# ── P2 §18: Stock-specific regime ────────────────────────────────────────────

_REGIME_PRIORITY = {
    "trending_bull":   3,
    "ranging":         2,
    "high_volatility": 1,
    "trending_bear":   0,
}


def compute_stock_regime(symbol: str, lookback: int = 60) -> Optional[dict]:
    """Compute regime for a single stock from its local price_history.parquet.

    Returns the same dict shape as RegimeAgent.run().data, or None if data
    is unavailable.  Does NOT make any network calls.
    """
    from core.knowledge_base import kb_path

    path = kb_path(symbol) / "price_history.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna(subset=["High", "Low", "Close"])
        if len(df) < lookback:
            return None

        df = df.tail(lookback).copy()
        high, low, close = df["High"], df["Low"], df["Close"]

        adx        = compute_adx(high, low, close, 14)
        ret_20d    = float((close.iloc[-1] / close.iloc[-20] - 1) * 100)
        volatility = float(close.pct_change().tail(20).std() * np.sqrt(252) * 100)

        if adx > 25 and ret_20d > 2:
            regime = "trending_bull"
        elif adx > 25 and ret_20d < -2:
            regime = "trending_bear"
        elif volatility > 20:
            regime = "high_volatility"
        else:
            regime = "ranging"

        confidence = min(1.0, 0.5 + abs(adx - 25) / 50 + abs(ret_20d) / 20)

        return {
            "regime":     regime,
            "confidence": round(confidence, 3),
            "adx":        round(adx, 2),
            "volatility": round(volatility, 2),
            "return_20d": round(ret_20d, 2),
            "source":     "stock",
        }
    except Exception as e:
        logger.debug("compute_stock_regime(%s) failed: %s", symbol, e)
        return None


def blend_regimes(nifty_regime: dict, stock_regime: Optional[dict],
                  stock_weight: float = 0.4) -> dict:
    """Blend NIFTY-level and stock-level regimes.

    When the stock is in a bear regime inside a bull market (or vice versa),
    the blended result reflects the stock's divergence.

    ``stock_weight`` controls how much the stock-specific signal overrides
    the market regime (default 40%).  The remaining 60% comes from NIFTY.

    Returns a dict with the same keys as RegimeAgent output, plus
    ``stock_regime`` and ``blend_note``.
    """
    if stock_regime is None:
        return {**nifty_regime, "stock_regime": None, "blend_note": "nifty_only"}

    nifty_r = nifty_regime.get("regime", "ranging")
    stock_r = stock_regime.get("regime", "ranging")

    nifty_pri = _REGIME_PRIORITY.get(nifty_r, 2)
    stock_pri = _REGIME_PRIORITY.get(stock_r, 2)

    blended_pri = nifty_pri * (1 - stock_weight) + stock_pri * stock_weight
    sorted_regimes = sorted(_REGIME_PRIORITY.items(), key=lambda x: abs(x[1] - blended_pri))
    blended_regime = sorted_regimes[0][0]

    blended_conf = (
        nifty_regime.get("confidence", 0.5) * (1 - stock_weight)
        + stock_regime.get("confidence", 0.5) * stock_weight
    )

    note = "aligned" if nifty_r == stock_r else f"divergent({nifty_r}↔{stock_r})"

    return {
        **nifty_regime,
        "regime":       blended_regime,
        "confidence":   round(blended_conf, 3),
        "stock_regime": stock_r,
        "blend_note":   note,
    }


if __name__ == '__main__':
    agent = RegimeAgent()
    result = agent.run()
    if result.ok():
        d = result.data
        print(f"\n{'='*50}")
        print(f"  MARKET REGIME: {d['regime'].upper()}")
        print(f"  Confidence:    {d['confidence']}")
        print(f"  ADX(14):       {d['adx']}")
        print(f"  20d Return:    {d['return_20d']}%")
        print(f"  Volatility:    {d['volatility']}% (annualized)")
        print(f"  India VIX:     {d['india_vix']}")
        print(f"  Strategy:      {d['strategy_adjustments']}")
        print(f"{'='*50}\n")
    else:
        print(f"ERROR: {result.error}")
