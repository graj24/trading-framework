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
  PM heartbeats: 08:30, 09:15, 11:00, 12:30, 14:00, 15:30 (all active PMs)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from dotenv import load_dotenv

from core.config import get_config

logger = logging.getLogger(__name__)


def today_pnl_pct(capital: float, db_path=None) -> float:
    """Module-level wrapper so tests can patch core.scheduler.today_pnl_pct."""
    from agents.execution_agent import today_pnl_pct as _fn
    return _fn(capital, db_path=db_path)


def _load_config() -> dict:
    return get_config()


# ── Multica heartbeat ─────────────────────────────────────────────────────────

def _multica_wakeup(pm_id: str, shift: str):
    """Publish pm.wakeup event to the bus. Optionally also POST to Multica if token is set."""
    import requests
    from core.pm_state import build_wakeup_context

    # Always publish to event bus — strategist daemon subscribes to this
    try:
        from core.event_bus import get_bus
        get_bus().publish(
            f"pm.wakeup.{pm_id}",
            {"trigger": "heartbeat", "shift": shift},
            pm_id=pm_id,
            severity="INFO",
        )
        logger.info(f"PM{pm_id} wakeup published to event bus [{shift}]")
    except Exception as e:
        logger.error(f"PM{pm_id} event bus wakeup failed: {e}")

    # Optionally also notify Multica (for issue-based workflow)
    server = os.getenv("MULTICA_SERVER_URL", "")
    token = os.getenv("MULTICA_TOKEN", "")
    if not server or not token:
        return

    context = build_wakeup_context(pm_id, shift=shift)
    payload = {
        "title": f"[{shift}] PM{pm_id} shift check",
        "body": context,
        "assignee": f"PM{pm_id}",
        "labels": ["heartbeat", shift.lower().replace(":", "")],
    }
    try:
        resp = requests.post(
            f"{server}/api/issues",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info(f"PM{pm_id} wakeup also issued via Multica [{shift}]")
        else:
            logger.warning(f"Multica wakeup failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.debug(f"Multica wakeup error (non-critical): {e}")


def job_pm_heartbeat(shift: str):
    """Wake all active PMs for a scheduled shift."""
    try:
        from core.pm_runtime import list_pms
        from pathlib import Path as _Path
        for pm in list_pms(active_only=True):
            pm_id = pm["pm_id"]
            if _Path(f"pm_{pm_id}/state/PAUSED").exists():
                logger.info(f"PM{pm_id} paused — skipping heartbeat [{shift}]")
                continue
            _multica_wakeup(pm_id, shift)
    except Exception as e:
        logger.error(f"PM heartbeat [{shift}] failed: {e}")


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
        from core.holidays import is_trading_day
        if not is_trading_day(datetime.now().date()):
            logger.info("  Market closed today — skipping pre-open scan")
            return
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
        # Anomaly alert: if scan returned no results at all, something is wrong
        all_preopen = result.get("all_preopen", result.get("buy_signals", []) + result.get("avoid_signals", []))
        if not all_preopen:
            alerter.send("⚠️ ANOMALY: Pre-open scan returned 0 results — data feed may be down")
            logger.warning("Pre-open scan returned 0 results — possible data feed issue")
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
        from common.core.pm_runtime import list_pms
        from common.core.pm_watchlist import add_to_pm_watchlist
        config = _load_config()
        result = DiscoveryAgent(config).discover(top_n=10)
        candidates = result.get("candidates", [])
        top_symbols = [c["symbol"] for c in candidates[:5]]
        # Add discovered symbols to every active PM's watchlist
        for pm in list_pms(active_only=True):
            added = add_to_pm_watchlist(pm["pm_id"], top_symbols)
            if added:
                logger.info(f"  PM{pm['pm_id']} watchlist updated: +{added}")
        logger.info(f"  Discovered {len(candidates)} candidates")
        for c in candidates[:5]:
            logger.info(f"  #{candidates.index(c)+1} {c['symbol']} score={c['score']:.1f} — {c['reasons'][0] if c['reasons'] else ''}")
    except Exception as e:
        logger.error(f"Discovery failed: {e}")


def job_pre_market_analysis():
    logger.info("🔍 [08:30] Pre-market analysis...")
    try:
        from core.holidays import is_trading_day
        if not is_trading_day(datetime.now().date()):
            logger.info("  Market closed today — skipping pre-market analysis")
            return
        from agents.technical_agent import TechnicalAgent
        from agents.regime_agent import RegimeAgent
        from common.core.pm_runtime import list_pms
        from common.core.pm_watchlist import get_pm_watchlist
        config = _load_config()
        regime = RegimeAgent(config).run({})
        logger.info(f"Regime: {regime.data.get('regime', 'unknown')}")
        for pm in list_pms(active_only=True):
            watchlist = get_pm_watchlist(pm["pm_id"], config)
            for symbol in watchlist:
                tech = TechnicalAgent(config).run({"symbol": symbol})
                logger.info(f"  PM{pm['pm_id']} {symbol}: score={tech.data.get('technical_score', 0)}/10")
    except Exception as e:
        logger.error(f"Pre-market analysis failed: {e}")


def job_execute_trades():
    logger.info("⚡ [09:15] Executing paper trades...")
    try:
        from core.holidays import is_trading_day
        if not is_trading_day(datetime.now().date()):
            logger.info("  Market closed today — skipping trade execution")
            return
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        from agents.master import MasterAgent
        from agents.execution_agent import ExecutionAgent
        from core.alerts import TelegramAlerter
        from common.core.pm_runtime import list_pms
        from common.core.pm_watchlist import get_pm_watchlist
        config = _load_config()
        alerter = TelegramAlerter()

        # Fetch all open symbols once
        open_symbols: set[str] = set()
        _db = _Path("paper_trades.db")
        if _db.exists():
            with _sqlite3.connect(_db) as _conn:
                open_symbols = {r[0] for r in _conn.execute(
                    "SELECT symbol FROM trades WHERE outcome='open'"
                ).fetchall()}

        for pm in list_pms(active_only=True):
            pm_id = pm["pm_id"]
            watchlist = get_pm_watchlist(pm_id, config)
            if not watchlist:
                logger.info(f"  PM{pm_id}: empty watchlist, skipping")
                continue

            # Each PM uses its own MasterAgent instance (which may be overridden in pm_<id>/agents/)
            try:
                import importlib.util as _ilu
                pm_master_path = _Path(f"pm_{pm_id}/agents/master.py")
                if pm_master_path.exists():
                    spec = _ilu.spec_from_file_location(f"pm_{pm_id}.agents.master", pm_master_path)
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    master = mod.MasterAgent(config)
                else:
                    master = MasterAgent(config)
                master.pm_id = pm_id
            except Exception as _e:
                logger.warning(f"PM{pm_id} MasterAgent load failed ({_e}), using default")
                master = MasterAgent(config)
                master.pm_id = pm_id

            executor = ExecutionAgent(config)
            pm_open = set()  # track within this PM's run

            for symbol in watchlist:
                if symbol in open_symbols:
                    continue
                result = master.run_for_stock(symbol)
                if not result.ok():
                    continue
                d = result.data
                logger.info(f"  PM{pm_id} {symbol}: {d['decision']} (conf={d['confidence']}%) — {d['reasoning']}")

                if d["decision"] != "BUY" or symbol in pm_open:
                    continue
                if not d.get("entry_price") or not d.get("position_size"):
                    continue

                trade = executor.execute_trade(
                    symbol=symbol,
                    entry_price=d["entry_price"],
                    stop_loss=d["stop_loss"],
                    target=d["target"],
                    position_size=d["position_size"],
                    reasoning=d["reasoning"],
                    signals=d.get("agent_scores"),
                    pm_id=pm_id,
                )
                pm_open.add(symbol)
                open_symbols.add(symbol)
                alerter.trade_alert(symbol, "BUY", d["entry_price"],
                                    d["stop_loss"], d["target"], d["confidence"])
                logger.info(f"  PM{pm_id} trade executed: {trade}")
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")


def job_intraday_scan():
    """Run intraday pattern scanner — fires every 5 min during market hours."""
    from zoneinfo import ZoneInfo
    now = datetime.now(tz=ZoneInfo("Asia/Kolkata"))
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
            # Publish to event bus for PM daemons
            try:
                from core.event_bus import get_bus
                from core.pm_runtime import list_pms
                get_bus().publish(
                    f"price.spike.{r['symbol']}",
                    {"symbol": r["symbol"], "ltp": r["ltp"], "pct": r.get("pct", 0),
                     "pattern": p["pattern"], "confidence": p["confidence"],
                     "entry": r.get("entry", r["ltp"]), "sl": p.get("stop_loss"),
                     "target": p.get("target"), "description": p["description"]},
                    severity="HIGH",
                )
            except Exception as _e:
                logger.debug(f"Event bus publish failed: {_e}")
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
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        from agents.execution_agent import ExecutionAgent, today_pnl_pct
        from agents.news_agent import NewsAgent
        from core.alerts import TelegramAlerter
        config = _load_config()
        executor = ExecutionAgent(config)
        news_agent = NewsAgent(config)
        alerter = TelegramAlerter()

        # P&L limit proximity alert (fire at 75% of daily limit)
        capital = config.get("trading", {}).get("capital", 10000)
        max_loss_pct = config.get("risk", {}).get("max_loss_per_day_pct", 3.0)
        import core.scheduler as _self
        current_pnl_pct = _self.today_pnl_pct(capital)
        threshold = -max_loss_pct * 0.75
        if current_pnl_pct <= threshold:
            alerter.send(
                f"⚠️ P&L ALERT: Daily P&L at {current_pnl_pct:.1f}% "
                f"(limit: -{max_loss_pct:.1f}%, threshold: {threshold:.1f}%)"
            )

        # Check SL/target
        closed = executor.monitor_positions()
        for trade in closed:
            alerter.exit_alert(trade["symbol"], trade["outcome"],
                               trade["pnl_pct"], trade["pnl_inr"])
            # Publish fill/exit event to bus
            try:
                from core.event_bus import get_bus
                pm_id = trade.get("pm_id", "")
                get_bus().publish(
                    f"fill.{pm_id}" if pm_id else "fill.system",
                    {"symbol": trade["symbol"], "outcome": trade["outcome"],
                     "pnl_inr": trade.get("pnl_inr", 0), "pnl_pct": trade.get("pnl_pct", 0)},
                    pm_id=pm_id or None,
                    severity="INFO",
                )
            except Exception as _e:
                logger.debug(f"Event bus publish failed: {_e}")

        # Only check news for currently open positions, not entire watchlist
        open_symbols: list[str] = []
        _db = _Path("paper_trades.db")
        if _db.exists():
            with _sqlite3.connect(_db) as _conn:
                open_symbols = [r[0] for r in _conn.execute(
                    "SELECT DISTINCT symbol FROM trades WHERE outcome='open'"
                ).fetchall()]

        if open_symbols:
            alerts = news_agent.monitor_open_positions(open_symbols)
            for symbol, tier in alerts.items():
                # Publish news event to bus for PM daemons
                try:
                    from core.event_bus import get_bus
                    get_bus().publish(
                        f"news.{symbol}",
                        {"symbol": symbol, "tier": tier, "source": "news_monitor"},
                        severity="CRITICAL" if tier == 1 else "HIGH",
                    )
                except Exception as _e:
                    logger.debug(f"Event bus publish failed: {_e}")
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
        import json as _json
        import sqlite3 as _sqlite3
        from datetime import date as _date
        from pathlib import Path as _Path
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

        # Update signal weights from today's closed trades
        _db = _Path("paper_trades.db")
        if _db.exists():
            today = _date.today().isoformat()
            with _sqlite3.connect(_db) as _conn:
                _conn.row_factory = _sqlite3.Row
                closed_today = _conn.execute(
                    "SELECT * FROM trades WHERE exit_date LIKE ? AND outcome != 'open'",
                    (f"{today}%",),
                ).fetchall()
            for t in closed_today:
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
                logger.info(f"  LearningAgent updated weights: {t['symbol']} ({outcome})")

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

    # PM heartbeat shifts (IST) — wake all active PMs at each shift
    for shift_label, h, m in [("08:30",8,30),("09:15",9,15),("11:00",11,0),
                               ("12:30",12,30),("14:00",14,0),("15:30",15,30)]:
        scheduler.add_job(
            job_pm_heartbeat, CronTrigger(hour=h, minute=m),
            args=[shift_label], id=f"pm_hb_{shift_label.replace(':','')}",
        )

    logger.info("Scheduler started (with PM heartbeats). Press Ctrl+C to stop.")
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
