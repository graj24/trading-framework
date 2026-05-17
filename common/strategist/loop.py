"""Per-PM strategist brain — the 24/7 autonomous loop.

Each PM runs one instance of this. It wakes on:
  - pm.wakeup.<pm_id>  events (heartbeat shifts, event-driven)
  - A slow background interval (off-shift research cadence)

Each cycle follows 7 steps:
  1. Read own state (plan, positions, journal, inbox, active strategy)
  2. Read rival snapshot (P&L, win-rate, recent trades, strategy version)
  3. Drain inbox
  4. Decide action: DO_NOTHING | RESEARCH | TRADE | EVOLVE | PIVOT
  5. Execute action (publish events, update state)
  6. Journal the cycle
  7. Emit agent.thinking.<pm_id> for UI

Usage:
    python -m common.strategist --pm_id 1
    python -m common.strategist --pm_id 1 --once   # single cycle then exit
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Valid cycle actions
ACTIONS = ("DO_NOTHING", "RESEARCH", "TRADE", "EVOLVE", "PIVOT")


class Strategist:
    def __init__(self, pm_id: str):
        self.pm_id = pm_id
        self._cursor_path = Path(f"pm_{pm_id}/state/strategist_cursor.txt")
        self._cycles_path = Path(f"pm_{pm_id}/state/cycles.jsonl")
        self._cycles_path.parent.mkdir(parents=True, exist_ok=True)

    # ── State readers ─────────────────────────────────────────────────────────

    def _read_state(self) -> dict:
        from common.core.pm_state import read_plan, read_positions, read_journal, drain_inbox
        from common.strategy.registry import load_active, get_active_version
        from common.core.pm_watchlist import get_pm_watchlist
        from common.core.config import get_config
        return {
            "plan": read_plan(self.pm_id),
            "positions": read_positions(self.pm_id),
            "journal_tail": read_journal(self.pm_id)[-3000:],  # last 3k chars
            "inbox": drain_inbox(self.pm_id),
            "active_strategy": load_active(self.pm_id),
            "active_version": get_active_version(self.pm_id),
            "watchlist": get_pm_watchlist(self.pm_id, get_config()),
        }

    def _read_rival_snapshot(self) -> dict:
        from common.leaderboard.snapshot import get_leaderboard, get_pm_stats
        try:
            board = get_leaderboard()
            rivals = [p for p in board if p["pm_id"] != self.pm_id]
            if not rivals:
                return {"rivals": [], "leaderboard": board}
            return {
                "rivals": rivals,
                "leaderboard": board,
                "top_rival": rivals[0],
            }
        except Exception as e:
            logger.debug(f"Rival snapshot failed: {e}")
            return {"rivals": [], "leaderboard": []}

    # ── LLM decision ──────────────────────────────────────────────────────────

    def _decide(self, state: dict, rival: dict, trigger: str) -> dict:
        """Call LLM to decide cycle action. Falls back to DO_NOTHING on error."""
        try:
            import litellm
            from common.core.config import get_config
            cfg = get_config()
            llm_model = cfg.get("llm", {}).get("model", "groq/llama-3.3-70b-versatile")

            # Use cheap model for off-shift research cycles
            if trigger.startswith("interval:"):
                llm_model = "groq/llama-3.1-8b-instant"

            prompt = self._build_prompt(state, rival, trigger)
            resp = litellm.completion(
                model=llm_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            # Validate action
            if result.get("action") not in ACTIONS:
                result["action"] = "DO_NOTHING"
            return result
        except Exception as e:
            logger.warning(f"PM{self.pm_id} strategist LLM failed: {e} — defaulting to DO_NOTHING")
            return {"action": "DO_NOTHING", "reasoning": f"LLM unavailable: {e}"}

    def _build_prompt(self, state: dict, rival: dict, trigger: str) -> str:
        positions_str = json.dumps(state["positions"], indent=2) if state["positions"] else "none"
        rival_str = json.dumps(rival.get("leaderboard", []), indent=2)
        inbox_str = json.dumps(state["inbox"][-10:], indent=2) if state["inbox"] else "none"
        strategy_name = (state["active_strategy"] or {}).get("name", "blank")
        watchlist = state["watchlist"]

        return f"""You are PM{self.pm_id}, an autonomous portfolio manager competing against other PMs.
Your goal: generate more P&L than every other PM.

TRIGGER: {trigger}
TIME: {datetime.now().strftime('%Y-%m-%d %H:%M IST')}

CURRENT STRATEGY: {strategy_name} (v{state['active_version']})
WATCHLIST: {watchlist}
OPEN POSITIONS: {positions_str}
RECENT INBOX EVENTS: {inbox_str}

LEADERBOARD:
{rival_str}

YOUR PLAN:
{state['plan'][:1000]}

RECENT JOURNAL:
{state['journal_tail'][-500:]}

Decide what to do this cycle. Return ONLY valid JSON:
{{
  "action": "DO_NOTHING|RESEARCH|TRADE|EVOLVE|PIVOT",
  "reasoning": "one sentence",
  "details": {{
    // For TRADE: {{"symbol": "X", "direction": "BUY|SELL", "qty": N, "sl": 0.0, "tag": "pm{self.pm_id}_..."}}
    // For RESEARCH: {{"question": "what to research", "priority": "high|medium"}}
    // For EVOLVE: {{"hypothesis": "what to change and why"}}
    // For PIVOT: {{"new_direction": "brief description of new strategy"}}
    // For DO_NOTHING: {{}}
  }}
}}"""

    # ── Action handlers ───────────────────────────────────────────────────────

    def _handle_trade(self, details: dict):
        """Publish exec_order event for the PM Trader daemon to pick up."""
        from common.core.event_bus import get_bus
        symbol = details.get("symbol", "")
        if not symbol:
            return
        payload = {
            "symbol": symbol,
            "qty": details.get("qty", 1),
            "order_type": "MARKET",
            "price": 0,
            "sl": details.get("sl", 0.0),
            "tag": details.get("tag", f"pm{self.pm_id}_strategist"),
            "direction": details.get("direction", "BUY"),
        }
        get_bus().publish(f"exec_order.{self.pm_id}", payload, pm_id=self.pm_id, severity="HIGH")
        logger.info(f"PM{self.pm_id} Strategist → exec_order: {symbol} {details.get('direction','BUY')}")

    def _handle_research(self, details: dict):
        """Run inline research: ask LLM the question with watchlist context, write findings to journal."""
        from common.core.event_bus import get_bus
        from common.core.pm_state import append_journal

        question = details.get("question", "").strip()
        priority = details.get("priority", "medium")

        # Always emit the event for any external consumers (researcher daemon when added)
        get_bus().publish(
            f"research.{self.pm_id}",
            {"question": question, "priority": priority},
            pm_id=self.pm_id, severity="INFO",
        )

        if not question:
            return

        # Run inline research via LLM so the next cycle can act on findings
        try:
            import litellm
            state = self._read_state()
            watchlist = state.get("watchlist", [])[:20]
            prompt = (
                f"You are PM{self.pm_id}'s research analyst.\n"
                f"Question: {question}\n"
                f"Watchlist: {watchlist}\n\n"
                "Provide a concise (≤120 words) actionable research note covering:\n"
                "- Top 1–3 NSE symbols from the watchlist that match the question\n"
                "- Why (technical setup, sector momentum, or news angle)\n"
                "- A specific entry/exit hypothesis you would test next cycle\n"
                "Be direct. No preamble."
            )
            resp = litellm.completion(
                model="groq/llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
            )
            findings = resp.choices[0].message.content.strip()
            append_journal(
                self.pm_id,
                f"**Research finding** ({priority}): {question}\n\n{findings}",
            )
            logger.info(f"PM{self.pm_id} research complete: {findings[:80]}…")
        except Exception as e:
            logger.warning(f"PM{self.pm_id} inline research failed: {e}")
            append_journal(self.pm_id, f"**Research queued** ({priority}): {question} — LLM unavailable")

    def _handle_evolve(self, details: dict, state: dict):
        """Ask LLM to propose a strategy diff, backtest it, commit if Sharpe improves."""
        hypothesis = details.get("hypothesis", "")
        if not hypothesis:
            return
        try:
            import litellm
            from common.strategy.registry import commit_new_version, load_active, get_active_version
            from common.strategy.backtest_gate import backtest_strategy

            current = load_active(self.pm_id) or {}
            current_ver = get_active_version(self.pm_id)

            prompt = f"""You are PM{self.pm_id}. Your current strategy is:
{json.dumps(current, indent=2)}

Hypothesis for improvement: {hypothesis}

Propose an updated strategy as a JSON object with these fields:
name, description, watchlist (list of NSE symbols), pipeline, gates (dict), sizing (dict),
data_sources (list), autonomy (dict with can_short, can_fno, universe).

Return ONLY valid JSON."""
            resp = litellm.completion(
                model="groq/llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            new_strategy = json.loads(raw)

            # Backtest gate: only commit if new Sharpe >= current Sharpe
            current_bt = backtest_strategy(current)
            new_bt = backtest_strategy(new_strategy)
            current_sharpe = current_bt["sharpe"]
            new_sharpe = new_bt["sharpe"]

            if new_sharpe >= current_sharpe or current_bt["n_trades"] == 0:
                new_ver = commit_new_version(
                    self.pm_id, new_strategy,
                    parent_version=current_ver,
                    notes=f"{hypothesis[:200]} | backtest: sharpe {current_sharpe:.2f}→{new_sharpe:.2f}",
                )
                logger.info(f"PM{self.pm_id} strategy evolved: v{current_ver}→v{new_ver} (sharpe {current_sharpe:.2f}→{new_sharpe:.2f})")
                from common.core.event_bus import get_bus
                get_bus().publish(
                    f"strategy.update.{self.pm_id}",
                    {"from_version": current_ver, "to_version": new_ver,
                     "hypothesis": hypothesis, "sharpe_before": current_sharpe, "sharpe_after": new_sharpe},
                    pm_id=self.pm_id, severity="INFO",
                )
            else:
                logger.info(f"PM{self.pm_id} evolution rejected: sharpe {current_sharpe:.2f} → {new_sharpe:.2f} (no improvement)")
                from common.core.pm_state import append_journal
                append_journal(self.pm_id,
                    f"**EVOLVE rejected** — hypothesis: {hypothesis[:100]} | "
                    f"backtest sharpe {current_sharpe:.2f}→{new_sharpe:.2f} (no improvement)")
        except Exception as e:
            logger.warning(f"PM{self.pm_id} evolve failed: {e}")

    def _handle_pivot(self, details: dict, state: dict):
        """Full replan: update plan.md and queue research."""
        from common.core.pm_state import write_plan
        new_direction = details.get("new_direction", "")
        if new_direction:
            new_plan = f"# PM{self.pm_id} Strategy Plan\n\n## Direction\n{new_direction}\n\n## Status: PIVOTING\n"
            write_plan(self.pm_id, new_plan)
            self._handle_research({"question": f"Research needed for pivot: {new_direction}", "priority": "high"})

    # ── Cycle ─────────────────────────────────────────────────────────────────

    def run_cycle(self, trigger: str = "manual") -> dict:
        """Run one full strategist cycle. Returns the cycle result dict."""
        ts = datetime.now()
        logger.info(f"PM{self.pm_id} Strategist cycle start [{trigger}]")

        # Emit thinking start
        self._emit_thinking("start", f"Starting cycle [{trigger}]")

        # Steps 1-3: read state
        state = self._read_state()
        rival = self._read_rival_snapshot()

        # Step 4: decide
        decision = self._decide(state, rival, trigger)
        action = decision.get("action", "DO_NOTHING")
        reasoning = decision.get("reasoning", "")
        details = decision.get("details", {})

        # Step 5: execute
        try:
            if action == "TRADE":
                self._handle_trade(details)
            elif action == "RESEARCH":
                self._handle_research(details)
            elif action == "EVOLVE":
                self._handle_evolve(details, state)
            elif action == "PIVOT":
                self._handle_pivot(details, state)
        except Exception as e:
            logger.error(f"PM{self.pm_id} action {action} failed: {e}")

        # Step 6: journal
        from common.core.pm_state import append_journal
        rival_summary = ""
        if rival.get("leaderboard"):
            top = rival["leaderboard"][0]
            rival_summary = f" | Leader: PM{top['pm_id']} +₹{top.get('total_pnl', 0):.0f}"
        append_journal(
            self.pm_id,
            f"**[{trigger}]** Action: `{action}` — {reasoning}{rival_summary}\n"
            + (f"Details: {json.dumps(details)}" if details else ""),
        )

        # Step 7: emit thinking done
        self._emit_thinking("done", f"{action}: {reasoning}")

        # Write cycle record
        cycle = {
            "ts": ts.isoformat(),
            "trigger": trigger,
            "action": action,
            "reasoning": reasoning,
            "details": details,
        }
        with self._cycles_path.open("a") as f:
            f.write(json.dumps(cycle) + "\n")

        logger.info(f"PM{self.pm_id} Strategist cycle done: {action} — {reasoning}")
        return cycle

    def _emit_thinking(self, status: str, context: str):
        try:
            from common.core.event_bus import get_bus
            get_bus().publish(
                f"agent.thinking.{self.pm_id}",
                {"agent": "strategist", "status": status, "context": context},
                pm_id=self.pm_id,
            )
        except Exception:
            pass

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run_forever(self, off_shift_interval_min: int = 15):
        """Block forever: react to wakeup events + run off-shift research cadence."""
        from common.core.event_bus import get_bus
        from common.core.pm_runtime import get_pm_config

        cfg = get_pm_config(self.pm_id)
        interval_min = cfg.get("off_shift_interval_min", off_shift_interval_min)

        # Restore cursor
        cursor = int(self._cursor_path.read_text().strip()) if self._cursor_path.exists() else get_bus().latest_id()
        logger.info(f"PM{self.pm_id} Strategist started — cursor={cursor}, interval={interval_min}min")

        last_interval_cycle = time.time()

        while True:
            try:
                # Check for wakeup events
                events = get_bus().subscribe(f"pm.wakeup.{self.pm_id}", since_id=cursor)
                for ev in events:
                    cursor = ev["id"]
                    payload = ev.get("payload", {}) or {}
                    base = payload.get("trigger") or payload.get("reason") or "event"
                    shift = payload.get("shift")
                    trigger = f"{base}:{shift}" if shift else str(base)
                    self.run_cycle(trigger)
                self._cursor_path.write_text(str(cursor))

                # Off-shift interval cycle
                now = time.time()
                if now - last_interval_cycle >= interval_min * 60:
                    self.run_cycle("interval:research")
                    last_interval_cycle = now

            except Exception as e:
                logger.error(f"PM{self.pm_id} Strategist loop error: {e}")

            time.sleep(5)
