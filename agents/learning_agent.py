"""
Learning Agent — updates per-stock signal weights based on trade outcomes.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.base import Agent, AgentResult
from core.knowledge_base import read_kb, write_kb

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "paper_trades.db"

WEIGHT_SIGNALS = ["technical_score", "news_sentiment", "pattern_ev", "sector_momentum", "regime_alignment"]
WIN_BOOST = 1.05
LOSS_DECAY = 0.97
MIN_WEIGHT = 0.1
MAX_WEIGHT = 3.0


class LearningAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("LearningAgent", config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        ctx = context or {}
        symbol = ctx.get("symbol")
        outcome = ctx.get("trade_outcome")
        signals = ctx.get("signals_at_entry", {})

        if symbol and outcome and signals:
            self.update_weights(symbol, outcome, signals)
            return self._result({"symbol": symbol, "weights_updated": True, "outcome": outcome})
        return self._result({"weights_updated": False, "status": "no_context"})

    def update_weights(self, symbol: str, trade_outcome: str, signals_at_entry: dict) -> dict:
        """Update signal weights based on trade outcome."""
        weights = read_kb(symbol, "signal_weights.json")
        if not weights:
            weights = {s: 1.0 for s in WEIGHT_SIGNALS}

        for signal in WEIGHT_SIGNALS:
            val = signals_at_entry.get(signal, 0)
            # Determine if signal was "positive" (bullish)
            is_positive = (
                (signal == "technical_score" and val > 5) or
                (signal != "technical_score" and val > 0)
            )
            if not is_positive:
                continue

            current = float(weights.get(signal, 1.0))
            if trade_outcome == "win":
                weights[signal] = round(min(MAX_WEIGHT, current * WIN_BOOST), 4)
            elif trade_outcome in ("loss", "timeout"):
                weights[signal] = round(max(MIN_WEIGHT, current * LOSS_DECAY), 4)

        weights["updated_at"] = datetime.now().isoformat()
        write_kb(symbol, "signal_weights.json", weights)
        logger.info(f"Weights updated for {symbol} after {trade_outcome}: {weights}")
        return weights

    def weekly_analysis(self, symbol: str) -> str:
        """Compute weekly performance stats from trade history."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            trades = conn.execute("""
                SELECT * FROM trades
                WHERE symbol=? AND outcome != 'open'
                ORDER BY exit_date DESC LIMIT 20
            """, (symbol,)).fetchall()
            conn.close()
        except Exception:
            return f"{symbol}: No trade history yet."

        if not trades:
            return f"{symbol}: No closed trades yet."

        pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        ev = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        return (
            f"{symbol} | Last {len(pnls)} trades | "
            f"Win rate: {win_rate:.0f}% | "
            f"Avg win: {avg_win:+.2f}% | "
            f"Avg loss: {avg_loss:+.2f}% | "
            f"EV: {ev:+.2f}%"
        )


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = LearningAgent(config)

    # Show before weights
    before = read_kb("RELIANCE", "signal_weights.json").copy()
    print(f"\nBefore: {before}")

    # Simulate a WIN trade
    signals = {"technical_score": 8, "news_sentiment": 0.3, "pattern_ev": 0.5,
               "sector_momentum": 0.2, "regime_alignment": 0.8}
    after = agent.update_weights("RELIANCE", "win", signals)
    print(f"After (win): {after}")

    # Simulate a LOSS trade
    after2 = agent.update_weights("RELIANCE", "loss", signals)
    print(f"After (loss): {after2}")

    print(f"\n{agent.weekly_analysis('RELIANCE')}")
