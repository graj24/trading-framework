"""Main entry point for the Autonomous Trading Framework."""
from __future__ import annotations

import argparse
import yaml
from dotenv import load_dotenv

from core.logger import setup_logging
from agents.master import MasterAgent


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    load_dotenv()
    config = load_config()
    setup_logging(config)

    import logging
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Autonomous Trading Framework")
    parser.add_argument("--schedule", action="store_true", help="Start 24/7 scheduler")
    parser.add_argument("--once", action="store_true", help="Run scheduler jobs once and exit")
    args = parser.parse_args()

    if args.schedule:
        from core.scheduler import start
        logger.info("Starting 24/7 scheduler...")
        start()
        return

    if args.once:
        from core.scheduler import run_once
        run_once()
        return

    # Default: single analysis cycle
    logger.info("=" * 60)
    logger.info("Autonomous Trading Framework")
    logger.info(f"Mode: {config['trading']['mode'].upper()} | Capital: ₹{config['trading']['capital']:,}")
    logger.info(f"Watchlist: {', '.join(config['watchlist'])}")
    logger.info("=" * 60)

    master = MasterAgent(config)
    from agents.execution_agent import ExecutionAgent, _get_ltp, _pnl
    from agents.learning_agent import LearningAgent
    executor = ExecutionAgent(config)
    learner  = LearningAgent(config)

    executed = []

    for symbol in config["watchlist"]:
        result = master.run_for_stock(symbol)
        if not result.ok():
            continue
        d = result.data
        logger.info(f"{symbol}: {d['decision']} (conf={d['confidence']}%) — {d['reasoning']}")

        if d["decision"] == "BUY" and d.get("entry_price", 0) > 0 and d.get("position_size", 0) > 0:
            # Skip if already holding this symbol
            import sqlite3
            from pathlib import Path
            db = Path("paper_trades.db")
            if db.exists():
                conn = sqlite3.connect(db)
                existing = conn.execute(
                    "SELECT id FROM trades WHERE symbol=? AND outcome='open'", (symbol,)
                ).fetchone()
                conn.close()
                if existing:
                    logger.info(f"  → {symbol}: position already open, skipping")
                    continue
            trade = executor.execute_trade(
                symbol=symbol,
                entry_price=d["entry_price"],
                stop_loss=d["stop_loss"],
                target=d["target"],
                position_size=d["position_size"],
                reasoning=d["reasoning"],
                signals=d.get("agent_scores"),
            )
            executed.append(trade)
            logger.info(f"  → Paper trade opened: {trade['trade_id']} | entry ₹{trade['entry_price']} | SL ₹{trade['stop_loss']} | T ₹{trade['target']}")

    # ── P&L Summary ──────────────────────────────────────────────────────────
    SEP = "=" * 60
    print(f"\n{SEP}")
    print(f"  WORKFLOW COMPLETE — P&L SUMMARY")
    print(SEP)
    print(f"  Signals generated : {len(config['watchlist'])} stocks analysed")
    print(f"  BUY trades opened : {len(executed)}")

    if executed:
        print(f"\n  {'SYMBOL':<12} {'ENTRY':>8} {'SL':>8} {'TARGET':>8} {'SIZE':>8}")
        print(f"  {'-'*50}")
        for t in executed:
            print(f"  {t['symbol']:<12} ₹{t['entry_price']:>7.2f} ₹{t['stop_loss']:>7.2f} ₹{t['target']:>7.2f} ₹{t['position_size']:>7.0f}")

    # Check all open positions against current price
    import sqlite3
    from pathlib import Path
    db = Path("paper_trades.db")
    if db.exists():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT * FROM trades WHERE outcome='open'").fetchall()
        closed_trades = conn.execute("SELECT * FROM trades WHERE outcome!='open'").fetchall()

        print(f"\n  Open positions    : {len(open_trades)}")
        if open_trades:
            print(f"\n  {'SYMBOL':<12} {'ENTRY':>8} {'LTP':>8} {'UNREAL P&L':>12} {'%':>7}")
            print(f"  {'-'*55}")
            total_unrealised = 0
            for t in open_trades:
                ltp = _get_ltp(t["symbol"])
                if ltp:
                    pct, inr = _pnl(t["entry_price"], ltp, t["position_size"])
                    total_unrealised += inr
                    flag = "🟢" if inr > 0 else "🔴"
                    print(f"  {t['symbol']:<12} ₹{t['entry_price']:>7.2f} ₹{ltp:>7.2f} ₹{inr:>+10.2f} {pct:>+6.2f}% {flag}")
            print(f"  {'':12} {'':>8} {'TOTAL':>8} ₹{total_unrealised:>+10.2f}")

        print(f"\n  Closed trades     : {len(closed_trades)}")
        if closed_trades:
            total_realised = sum(t["pnl_inr"] for t in closed_trades if t["pnl_inr"])
            wins = [t for t in closed_trades if t["pnl_inr"] and t["pnl_inr"] > 0]
            print(f"  Win rate          : {len(wins)}/{len(closed_trades)} ({len(wins)/len(closed_trades)*100:.0f}%)")
            print(f"  Total realised P&L: ₹{total_realised:+,.2f}")
            # Update signal weights from closed trades — read signals from stored JSON
            import json as _json
            for t in closed_trades:
                outcome = "win" if t["pnl_inr"] and t["pnl_inr"] > 0 else "loss"
                try:
                    signals = _json.loads(t["signals_json"]) if t["signals_json"] else {}
                except Exception:
                    signals = {}
                learner.update_weights(t["symbol"], outcome, {
                    "technical_score": signals.get("technical_score", 0),
                    "news_sentiment":  signals.get("sentiment", 0),
                    "pattern_ev":      signals.get("pattern_ev", 0),
                })
                logger.info(f"  → LearningAgent updated weights for {t['symbol']} ({outcome})")
        conn.close()
    else:
        print(f"\n  No trade history yet.")

    print(f"{SEP}\n")


if __name__ == "__main__":
    main()
