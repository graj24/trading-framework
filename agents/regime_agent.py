from __future__ import annotations
"""Market Regime Detection Agent — bull/bear/ranging/volatile classification."""
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

STRATEGY_ADJUSTMENTS = {
    'trending_bull': {'position_size_multiplier': 1.2, 'prefer': 'breakouts', 'avoid': 'mean_reversion'},
    'trending_bear': {'position_size_multiplier': 0.5, 'prefer': 'short_or_cash', 'avoid': 'longs'},
    'high_volatility': {'position_size_multiplier': 0.5, 'prefer': 'tight_stops', 'avoid': 'overnight'},
    'ranging': {'position_size_multiplier': 0.8, 'prefer': 'mean_reversion', 'avoid': 'breakouts'},
}


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Compute ADX(14) and return the latest value."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return float(adx.iloc[-1]) if not adx.empty else 0.0


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

            return self._result({
                'regime': regime,
                'confidence': round(confidence, 3),
                'adx': round(adx, 2),
                'volatility': round(volatility, 2),
                'return_20d': round(ret_20d, 2),
                'india_vix': round(vix_val, 2) if vix_val else None,
                'strategy_adjustments': strategy_adjustments,
            })
        except Exception as e:
            logger.exception("RegimeAgent failed")
            return self._error(str(e))


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
