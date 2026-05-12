"""
24/7 Trading Scheduler using APScheduler.

Schedule (IST):
  06:00 - Update knowledge bases
  08:30 - Pre-market analysis
  09:00 - Generate signals
  09:15 - Execute paper trades
  Every 5 min (09:15-15:00) - Monitor positions + news
  15:00 - Close all positions
  15:30 - Daily report + learning update
"""
from __future__ import annotations

import argparse
import logging
import yaml
from datetime import datetime
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ── Job functions ─────────────────────────────────────────────────────────────

def job_update_knowledge_bases():
    logger.info("📊 [06:00] Updating knowledge bases...")
    try:
        from agents.data_agent import DataAgent
        config = _load_config()
        agent = DataAgent(config)
        for symbol in config["watchlist"]:
            agent.build_kb(symbol)
        logger.info("Knowledge bases updated")
    except Exception as e:
        logger.error(f"KB update failed: {e}")


def job_earnings_evening_prep():
    logger.info("📅 [15:30] Earnings calendar — evening prep...")
    try:
        from agents.earnings_calendar_agent import EarningsCalendarAgent
        config = _load_config()
        result = EarningsCalendarAgent(config).evening_prep()
        watching = result.get("watching", [])
        if watching:
            for w in watching:
                logger.info(f"  ⚠️  {w['symbol']}: earnings in {w.get('days_away','?')} day(s) — avg reaction {w['historical_avg_reaction']:+.1f}%")
        else:
            logger.info("  No earnings in next 3 days")
    except Exception as e:
        logger.error(f"Earnings evening prep failed: {e}")


def job_earnings_overnight():
    logger.info("🌙 Overnight earnings monitor — checking filings...")
    try:
        from agents.earnings_calendar_agent import EarningsCalendarAgent
        config = _load_config()
        result = EarningsCalendarAgent(config).overnight_monitor()
        for s in result.get("signals", []):
            logger.info(f"  {s['symbol']}: {s['action']} — {s['reasoning']}")
    except Exception as e:
        logger.error(f"Overnight monitor failed: {e}")


def job_preopen_scan():
    logger.info("⚡ [09:00] Pre-open scan...")
    try:
        from agents.pre_open_monitor import PreOpenMonitor
        from agents.earnings_calendar_agent import EarningsCalendarAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        alerter = TelegramAlerter()

        # Morning earnings summary first
        earnings_morning = EarningsCalendarAgent(config).morning_scan()
        for s in earnings_morning.get("strong_buys", []) + earnings_morning.get("buys", []):
            logger.info(f"  🟢 Earnings signal: {s['symbol']} {s['action']} — {s['reasoning']}")

        # Pre-open price scan
        result = PreOpenMonitor(config).scan()
        for s in result.get("buy_signals", []):
            logger.info(f"  🟢 Gap-up: {s['symbol']} {s['gap_pct']:+.1f}% — {s['reasoning']}")
            alerter.send(f"⚡ PRE-OPEN GAP-UP: <b>{s['symbol']}</b> {s['gap_pct']:+.1f}%\n"
                         f"Entry: ₹{s['entry']} | SL: ₹{s['stop_loss']} | Target: ₹{s['target']}\n"
                         f"{s['reasoning']}")
        for s in result.get("avoid_signals", []):
            logger.info(f"  🔴 Gap-down: {s['symbol']} {s['gap_pct']:+.1f}% — {s['reasoning']}")
    except Exception as e:
        logger.error(f"Pre-open scan failed: {e}")


def job_discover_stocks():
    logger.info("🔍 [07:00] Discovering stocks from news + volume + bulk deals...")
    try:
        from agents.discovery_agent import DiscoveryAgent
        config = _load_config()
        result = DiscoveryAgent(config).discover(top_n=10)
        added = result.get("added_to_watchlist", [])
        candidates = result.get("candidates", [])
        logger.info(f"  Discovered {len(candidates)} candidates, added to watchlist: {added or 'none new'}")
        for c in candidates[:5]:
            logger.info(f"  #{candidates.index(c)+1} {c['symbol']} score={c['score']:.1f} — {c['reasons'][0] if c['reasons'] else ''}")
    except Exception as e:
        logger.error(f"Discovery failed: {e}")


def job_pre_market_analysis():
    logger.info("🔍 [08:30] Pre-market analysis...")
    try:
        from agents.technical_agent import TechnicalAgent
        from agents.regime_agent import RegimeAgent
        config = _load_config()
        regime = RegimeAgent(config).run({})
        logger.info(f"Regime: {regime.data.get('regime', 'unknown')}")
        for symbol in config["watchlist"]:
            tech = TechnicalAgent(config).run({"symbol": symbol})
            logger.info(f"  {symbol}: score={tech.data.get('technical_score', 0)}/10")
    except Exception as e:
        logger.error(f"Pre-market analysis failed: {e}")


def job_generate_signals():
    logger.info("🎯 [09:00] Generating signals...")
    try:
        from agents.master import MasterAgent
        config = _load_config()
        master = MasterAgent(config)
        for symbol in config["watchlist"]:
            result = master.run_for_stock(symbol)
            if result.ok():
                d = result.data
                logger.info(f"  {symbol}: {d['decision']} (conf={d['confidence']}%) — {d['reasoning']}")
    except Exception as e:
        logger.error(f"Signal generation failed: {e}")


def job_execute_trades():
    logger.info("⚡ [09:15] Executing paper trades...")
    try:
        from agents.master import MasterAgent
        from agents.execution_agent import ExecutionAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        master = MasterAgent(config)
        executor = ExecutionAgent(config)
        alerter = TelegramAlerter()

        for symbol in config["watchlist"]:
            result = master.run_for_stock(symbol)
            if result.ok() and result.data["decision"] == "BUY":
                d = result.data
                trade = executor.execute_trade(
                    symbol=symbol,
                    entry_price=d["entry_price"],
                    stop_loss=d["stop_loss"],
                    target=d["target"],
                    position_size=d["position_size"],
                    reasoning=d["reasoning"],
                )
                alerter.trade_alert(symbol, "BUY", d["entry_price"],
                                    d["stop_loss"], d["target"], d["confidence"])
                logger.info(f"  Trade executed: {trade}")
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")


def job_intraday_scan():
    """Run intraday pattern scanner — fires every 5 min during market hours."""
    now = datetime.now()
    # Only run during market hours 9:15 AM - 3:00 PM IST
    if not (9 * 60 + 15 <= now.hour * 60 + now.minute <= 15 * 60):
        return
    try:
        from agents.intraday_scanner import IntradayPatternScanner
        from agents.execution_agent import ExecutionAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        scanner = IntradayPatternScanner(config)
        executor = ExecutionAgent(config)
        alerter = TelegramAlerter()

        result = scanner.scan_all()
        for r in result.get("buy_signals", []):
            p = r["best_pattern"]
            logger.info(f"  🟢 INTRADAY: {r['symbol']} — {p['description']}")
            # Execute paper trade
            executor.execute_trade(
                symbol=r["symbol"],
                entry_price=r.get("entry", r["ltp"]),
                stop_loss=p.get("stop_loss", r["ltp"] * 0.99),
                target=p.get("target", r["ltp"] * 1.02),
                position_size=config["trading"]["capital"] * 0.1,
                reasoning=p["description"],
            )
            alerter.send(
                f"📊 INTRADAY PATTERN: <b>{r['symbol']}</b>\n"
                f"{p['pattern'].replace('_',' ').title()} (conf={p['confidence']}%)\n"
                f"Entry: ₹{r.get('entry', r['ltp']):.2f} | SL: ₹{p['stop_loss']:.2f} | T: ₹{p['target']:.2f}\n"
                f"{p['description']}"
            )
    except Exception as e:
        logger.error(f"Intraday scan failed: {e}")


def job_monitor_positions():
    logger.info("👁️  Monitoring positions...")
    try:
        from agents.execution_agent import ExecutionAgent
        from agents.news_agent import NewsAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        executor = ExecutionAgent(config)
        news_agent = NewsAgent(config)
        alerter = TelegramAlerter()

        # Check SL/target
        closed = executor.monitor_positions()
        for trade in closed:
            alerter.exit_alert(trade["symbol"], trade["outcome"],
                               trade["pnl_pct"], trade["pnl_inr"])

        # Check news for open positions
        alerts = news_agent.monitor_open_positions(config["watchlist"])
        for symbol, tier in alerts.items():
            if tier == 1:
                result = executor.emergency_exit(symbol, "TIER 1 news")
                if result:
                    alerter.emergency_alert(symbol, tier, "Emergency exit triggered")
    except Exception as e:
        logger.error(f"Position monitoring failed: {e}")


def job_close_all_positions():
    logger.info("🔒 [15:00] Closing all open positions...")
    try:
        from agents.execution_agent import ExecutionAgent
        import sqlite3
        from pathlib import Path
        config = _load_config()
        executor = ExecutionAgent(config)
        db = Path("paper_trades.db")
        if not db.exists():
            return
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        open_trades = conn.execute("SELECT symbol FROM trades WHERE outcome='open'").fetchall()
        conn.close()
        for t in open_trades:
            executor.emergency_exit(t["symbol"], "market_close")
        logger.info(f"Closed {len(open_trades)} positions at market close")
    except Exception as e:
        logger.error(f"Close all failed: {e}")


def job_prune_watchlist():
    """Prune stale stocks from watchlist, keep core + max 20 total."""
    try:
        import sqlite3
        from pathlib import Path
        config = _load_config()
        core = set(config.get("core_watchlist", []))
        watchlist = config.get("watchlist", [])
        max_size = config.get("watchlist_max", 20)

        if len(watchlist) <= max_size:
            return

        db = Path("paper_trades.db")
        recent_active = set()
        if db.exists():
            conn = sqlite3.connect(db)
            # Keep stocks with trades in last 5 days
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM trades WHERE created_at > datetime('now', '-5 days')"
            ).fetchall()
            recent_active = {r[0] for r in rows}
            conn.close()

        # Priority: core > recently traded > rest (by position in list = recency)
        pruned = list(core)
        for sym in watchlist:
            if sym in core:
                continue
            if sym in recent_active or len(pruned) < max_size:
                pruned.append(sym)
            if len(pruned) >= max_size:
                break

        removed = set(watchlist) - set(pruned)
        if removed:
            config["watchlist"] = pruned
            with open("config.yaml", "w") as f:
                import yaml
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"Watchlist pruned: removed {removed}, kept {len(pruned)} stocks")
    except Exception as e:
        logger.error(f"Watchlist pruning failed: {e}")


def job_post_market():
    logger.info("📋 [15:30] Post-market report...")
    try:
        from agents.execution_agent import ExecutionAgent
        from agents.learning_agent import LearningAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        executor = ExecutionAgent(config)
        learner = LearningAgent(config)
        alerter = TelegramAlerter()

        report = executor.daily_report()
        alerter.daily_summary(report)
        logger.info(f"Daily P&L: ₹{report['total_pnl_inr']:+.2f} ({report['total_pnl_pct']:+.2f}%)")

        for symbol in config["watchlist"]:
            analysis = learner.weekly_analysis(symbol)
            logger.info(f"  {analysis}")
    except Exception as e:
        logger.error(f"Post-market failed: {e}")


# ── Scheduler setup ───────────────────────────────────────────────────────────

def start():
    """Start the 24/7 blocking scheduler."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(job_update_knowledge_bases, CronTrigger(hour=6, minute=0))
    scheduler.add_job(job_discover_stocks, CronTrigger(hour=7, minute=0))
    scheduler.add_job(job_preopen_scan, CronTrigger(hour=9, minute=0))
    scheduler.add_job(job_pre_market_analysis, CronTrigger(hour=8, minute=30))
    scheduler.add_job(job_generate_signals, CronTrigger(hour=9, minute=0))
    scheduler.add_job(job_execute_trades, CronTrigger(hour=9, minute=15))
    scheduler.add_job(job_monitor_positions, IntervalTrigger(minutes=5),
                      id="monitor", start_date="2000-01-01 09:15:00")
    scheduler.add_job(job_intraday_scan, IntervalTrigger(minutes=5),
                      id="intraday", start_date="2000-01-01 09:15:00")
    scheduler.add_job(job_close_all_positions, CronTrigger(hour=15, minute=0))
    scheduler.add_job(job_post_market, CronTrigger(hour=15, minute=30))
    scheduler.add_job(job_earnings_evening_prep, CronTrigger(hour=15, minute=30))
    scheduler.add_job(job_prune_watchlist, CronTrigger(hour=15, minute=45))
    # Overnight earnings monitor every 30 min from 6 PM to 8 AM
    scheduler.add_job(job_earnings_overnight, CronTrigger(hour="18-23,0-8", minute="0,30"))

    logger.info("Scheduler started. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


def run_once():
    """Run all jobs once in sequence (for testing)."""
    logger.info("Running all scheduler jobs once...")
    job_update_knowledge_bases()
    job_earnings_overnight()
    job_discover_stocks()
    job_preopen_scan()
    job_pre_market_analysis()
    job_generate_signals()
    job_execute_trades()
    job_monitor_positions()
    job_post_market()
    logger.info("All jobs complete.")


if __name__ == "__main__":
    load_dotenv()
    from core.logger import setup_logging
    config = _load_config()
    setup_logging(config)

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run all jobs once and exit")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        start()
