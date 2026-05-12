from __future__ import annotations
"""Technical Analysis Agent — indicators, structure, multi-timeframe confluence."""
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series):
    macd_line = _ema(close, 12) - _ema(close, 26)
    signal_line = _ema(macd_line, 9)
    return macd_line, signal_line


def _bollinger(close: pd.Series, period: int = 20, std_dev: int = 2):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma + std_dev * std, sma, sma - std_dev * std


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # Zero out where the other is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr = _atr(high, low, close, period)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> float:
    typical = (high + low + close) / 3
    return (typical * volume).sum() / volume.sum()


def _find_support_resistance(df: pd.DataFrame, window: int = 5, min_touches: int = 3):
    """Find price levels where price reversed 3+ times in last 252 days."""
    recent = df.tail(252).copy()
    highs = recent['High']
    lows = recent['Low']
    # Local maxima and minima
    local_max = highs[(highs.shift(1) < highs) & (highs.shift(-1) < highs)]
    local_min = lows[(lows.shift(1) > lows) & (lows.shift(-1) > lows)]

    price_range = recent['High'].max() - recent['Low'].min()
    tolerance = price_range * 0.015  # 1.5% tolerance for clustering

    def cluster_levels(levels: pd.Series) -> list:
        if levels.empty:
            return []
        sorted_levels = sorted(levels.values)
        clusters: list[list[float]] = [[sorted_levels[0]]]
        for lvl in sorted_levels[1:]:
            if lvl - clusters[-1][0] <= tolerance:
                clusters[-1].append(lvl)
            else:
                clusters.append([lvl])
        return [round(np.mean(c), 2) for c in clusters if len(c) >= min_touches]

    return cluster_levels(local_min), cluster_levels(local_max)


class TechnicalAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("TechnicalAgent", config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        symbol = (context or {}).get("symbol", "RELIANCE")
        path = BASE_DIR / "stocks" / symbol / "price_history.parquet"
        if not path.exists():
            return self._error(f"No price data for {symbol}")

        df = pd.read_parquet(path).dropna(subset=["Close"])
        if len(df) < 200:
            return self._error(f"Insufficient data for {symbol}: {len(df)} rows")

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        price = close.iloc[-1]

        # Trend
        ema20 = _ema(close, 20).iloc[-1]
        ema50 = _ema(close, 50).iloc[-1]
        ema200 = _ema(close, 200).iloc[-1]

        # Momentum
        rsi = _rsi(close).iloc[-1]
        macd_line, signal_line = _macd(close)
        macd_val = macd_line.iloc[-1]
        signal_val = signal_line.iloc[-1]

        # Volatility
        bb_upper, bb_mid, bb_lower = _bollinger(close)
        atr = _atr(high, low, close).iloc[-1]

        # Volume
        obv = _obv(close, volume)
        obv_rising = obv.iloc[-1] > obv.iloc[-6]  # last 5 days
        vwap = _vwap(high.tail(20), low.tail(20), close.tail(20), volume.tail(20))

        # ADX
        adx = _adx(high, low, close).iloc[-1]

        # Composite score
        score = 0
        if price > ema20:
            score += 1
        if price > ema50:
            score += 1
        if price > ema200:
            score += 1
        if 40 <= rsi <= 60:
            score += 1
        if macd_val > signal_val:
            score += 1
        if price > vwap:
            score += 1
        if obv_rising:
            score += 1
        if adx > 25:
            score += 1
        if price < bb_upper.iloc[-1] * 0.98:  # not near upper band
            score += 1
        if atr / price < 0.02:
            score += 1

        # MACD signal
        if macd_val > signal_val:
            macd_signal = "bullish"
        elif macd_val < signal_val:
            macd_signal = "bearish"
        else:
            macd_signal = "neutral"

        # Trend direction
        if price > ema50 and ema20 > ema50:
            trend = "up"
        elif price < ema50 and ema20 < ema50:
            trend = "down"
        else:
            trend = "sideways"

        # Support/Resistance
        support_levels, resistance_levels = _find_support_resistance(df)

        # Break of Structure
        high_20 = high.rolling(20).max()
        vol_avg_20 = volume.rolling(20).mean()
        bos_detected = bool(
            high.iloc[-1] >= high_20.iloc[-2] and volume.iloc[-1] > vol_avg_20.iloc[-1]
        )

        vol_avg20 = volume.rolling(20).mean().iloc[-1]
        volume_ratio = round(float(volume.iloc[-1] / vol_avg20), 2) if vol_avg20 > 0 else 1.0

        data = {
            "symbol": symbol,
            "technical_score": score,
            "rsi": round(float(rsi), 2),
            "macd_signal": macd_signal,
            "trend": trend,
            "bos_detected": bos_detected,
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "ema20": round(float(ema20), 2),
            "ema50": round(float(ema50), 2),
            "ema200": round(float(ema200), 2),
            "adx": round(float(adx), 2),
            "atr": round(float(atr), 2),
            "price": round(float(price), 2),
            "volume_ratio": volume_ratio,
            "current_price": round(float(price), 2),
        }
        return self._result(data)


if __name__ == "__main__":
    agent = TechnicalAgent(config={})
    result = agent.run({"symbol": "RELIANCE"})
    if result.ok():
        d = result.data
        print(f"\n{'='*50}")
        print(f"  TECHNICAL SCORECARD: {d['symbol']}")
        print(f"{'='*50}")
        print(f"  Price:            ₹{d['price']}")
        print(f"  Technical Score:  {d['technical_score']}/10")
        print(f"  Trend:            {d['trend']}")
        print(f"  RSI(14):          {d['rsi']}")
        print(f"  MACD Signal:      {d['macd_signal']}")
        print(f"  ADX(14):          {d['adx']}")
        print(f"  ATR(14):          {d['atr']}")
        print(f"  EMA20:            ₹{d['ema20']}")
        print(f"  EMA50:            ₹{d['ema50']}")
        print(f"  EMA200:           ₹{d['ema200']}")
        print(f"  BOS Detected:     {d['bos_detected']}")
        print(f"  Support Levels:   {d['support_levels']}")
        print(f"  Resistance Levels:{d['resistance_levels']}")
        print(f"{'='*50}\n")
    else:
        print(f"ERROR: {result.error}")
