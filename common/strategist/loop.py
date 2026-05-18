"""Per-PM strategist brain — the 24/7 autonomous loop.

Each PM runs one instance of this. It wakes on:
  - pm.wakeup.<pm_id>  events (heartbeat shifts, event-driven)
  - A slow background interval (off-shift research cadence)

Each cycle follows 7 steps:
  1. Read own state (plan, positions, journal, inbox, active strategy)
  2. Read rival snapshot (P&L, win-rate, recent trades, strategy version)
  3. Drain inbox
  4. Decide action via tool-calling loop (LLM can call tools before deciding)
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

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

# Valid final actions
ACTIONS = ("DO_NOTHING", "RESEARCH", "TRADE", "EVOLVE", "PIVOT")

# Max tool calls per cycle (prevents runaway loops)
MAX_TOOL_CALLS = 25


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
            "journal_tail": read_journal(self.pm_id)[-3000:],
            "inbox": drain_inbox(self.pm_id),
            "active_strategy": load_active(self.pm_id),
            "active_version": get_active_version(self.pm_id),
            "watchlist": get_pm_watchlist(self.pm_id, get_config()),
        }

    def _read_rival_snapshot(self) -> dict:
        from common.leaderboard.snapshot import get_leaderboard
        try:
            board = get_leaderboard()
            rivals = [p for p in board if p["pm_id"] != self.pm_id]
            return {"rivals": rivals, "leaderboard": board,
                    "top_rival": rivals[0] if rivals else None}
        except Exception as e:
            logger.debug(f"Rival snapshot failed: {e}")
            return {"rivals": [], "leaderboard": []}

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _system_prompt(self, state: dict, rival: dict, trigger: str) -> str:
        positions_str = json.dumps(state["positions"], indent=2) if state["positions"] else "none"
        rival_str = json.dumps(rival.get("leaderboard", []), indent=2)
        inbox_str = json.dumps(state["inbox"][-10:], indent=2) if state["inbox"] else "none"
        strategy_name = (state["active_strategy"] or {}).get("name", "blank")
        watchlist = state["watchlist"]

        # Load PM identity (trading philosophy) if exists
        identity = ""
        try:
            id_path = Path(f"pm_{self.pm_id}/identity.md")
            if id_path.exists():
                identity = "\n\n## YOUR IDENTITY\n" + id_path.read_text()
        except Exception:
            pass

        cold_start = ""
        if strategy_name == "blank" and state.get("active_version") == 1:
            cold_start = """
COLD START — You have no strategy yet. This is your first cycle.
Choose your path and act on it immediately:

A. START BLANK — Invent your own strategy. Use web_search/web_fetch to research
   NSE sectors, then write a strategy YAML to pm_{pm_id}/strategies/v002.yaml
   and update your watchlist.

B. INHERIT FROM PM1 — Read pm_1/strategies/ to see PM1's current strategy,
   then write a modified version to pm_{pm_id}/strategies/v002.yaml.

C. RESEARCH FIRST — Use web_search + sql_query to study PM1's trade history
   and NSE market conditions. Store findings with memory_store.

D. COUNTER PM1 — Read PM1's strategy and trade history, identify weaknesses,
   build a targeted counter-strategy. Write it to pm_{pm_id}/strategies/v002.yaml.

Use your tools. Read files. Search the web. Write code. Then commit to a direction.
""".replace("{pm_id}", self.pm_id)

        return f"""You are PM{self.pm_id}, an autonomous portfolio manager competing against other PMs.
Your goal: generate more P&L than every other PM. You have full access to tools.
IMPORTANT: All prices are in Indian Rupees (₹). Never use $ or USD. Use ₹ for all prices and P&L.{identity}

TRIGGER: {trigger}
TIME: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}

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
{cold_start}
You can call tools (read_file, write_file, run_shell, run_pytest, web_fetch, web_search,
sql_query, get_prices, memory_store, memory_search) as many times as needed before deciding.
RULE: Before forming any entry/exit price hypothesis, call get_prices() to get real current prices.
Never guess or use training-data prices — NSE prices change daily.

EFFICIENCY RULES:
1. Start by calling memory_search to check what you already know — don't redo past research.
2. Aim to decide within 5-8 tool calls. Don't loop endlessly on web_search if it returns nothing.
3. If unsure, return DO_NOTHING with a clear reasoning. Better than hitting the tool call limit.
4. Only do EVOLVE/PIVOT if you have specific evidence — otherwise stay in your current strategy.

When done, return a JSON object as your final message:
{{
  "action": "DO_NOTHING|RESEARCH|TRADE|EVOLVE|PIVOT",
  "reasoning": "one sentence",
  "details": {{
    // TRADE:   {{"symbol": "X", "direction": "BUY|SELL", "qty": N, "sl": 0.0, "tag": "pm{self.pm_id}_..."}}
    // RESEARCH:{{"question": "...", "priority": "high|medium"}}
    // EVOLVE:  {{"hypothesis": "what to change and why"}}
    // PIVOT:   {{"new_direction": "brief description"}}
    // DO_NOTHING: {{}}
  }}
}}"""

    # ── Tool-calling decision loop ─────────────────────────────────────────────

    def _decide(self, state: dict, rival: dict, trigger: str) -> dict:
        """Run tool-calling loop until LLM returns a final action JSON."""
        try:
            import litellm
            from common.core.config import get_config
            from common.tools import get_tool_schemas, dispatch

            cfg = get_config()
            model = cfg.get("llm", {}).get("model", "openai/moonshotai/kimi-k2.6")
            # Note: 70b-versatile has 12000 TPM on free tier vs 6000 for 8b-instant
            # so we use the same model for off-shift to avoid rate limits.

            messages = [{"role": "system", "content": self._system_prompt(state, rival, trigger)}]
            tools = get_tool_schemas()
            tool_calls_made = 0

            while tool_calls_made < MAX_TOOL_CALLS:
                # Retry with backoff on 429
                for attempt in range(4):
                    try:
                        resp = litellm.completion(
                            model=model,
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            max_tokens=1000,
                            temperature=0.2,
                            api_base=cfg.get("llm", {}).get("api_base", "https://integrate.api.nvidia.com/v1"),
                            api_key=cfg.get("llm", {}).get("api_key") or __import__("os").getenv("NVIDIA_NIM_API_KEY"),
                        )
                        break
                    except Exception as e:
                        if "429" in str(e) and attempt < 3:
                            wait = 2 ** attempt * 5  # 5, 10, 20s
                            logger.warning(f"PM{self.pm_id} rate limited, retrying in {wait}s")
                            time.sleep(wait)
                        else:
                            raise
                msg = resp.choices[0].message

                # If LLM made tool calls, execute them and continue
                if getattr(msg, "tool_calls", None):
                    messages.append(msg)
                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments) or {}
                        except Exception:
                            args = {}
                        if not isinstance(args, dict):
                            args = {}
                        logger.info(f"PM{self.pm_id} tool: {fn_name}({list(args.keys())})")
                        result = dispatch(self.pm_id, fn_name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result)[:1500],
                        })
                        tool_calls_made += 1
                    continue

                # No tool calls — expect final JSON decision
                raw = (msg.content or "").strip()

                # Groq/llama sometimes wraps the final answer as a tool call
                # e.g. <function=DO_NOTHING>{"action":...}</function>
                if not raw and getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        if tc.function.name in ACTIONS:
                            try:
                                args = json.loads(tc.function.arguments) or {}
                                if not isinstance(args, dict):
                                    args = {}
                                # Reconstruct as if it were a plain JSON response
                                raw = json.dumps({
                                    "action": tc.function.name,
                                    "reasoning": args.get("reasoning", tc.function.name),
                                    "details": args.get("details", {}),
                                })
                            except Exception:
                                raw = json.dumps({"action": tc.function.name, "reasoning": "", "details": {}})
                            break
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                try:
                    result = json.loads(raw)
                    if result.get("action") not in ACTIONS:
                        result["action"] = "DO_NOTHING"
                    logger.info(f"PM{self.pm_id} used {tool_calls_made} tools this cycle")
                    return result
                except json.JSONDecodeError:
                    # LLM returned prose instead of JSON — treat as DO_NOTHING
                    logger.warning(f"PM{self.pm_id} non-JSON response: {raw[:100]}")
                    return {"action": "DO_NOTHING", "reasoning": raw[:200]}

            return {"action": "DO_NOTHING", "reasoning": f"Hit tool call limit ({MAX_TOOL_CALLS})"}

        except Exception as e:
            logger.warning(f"PM{self.pm_id} strategist LLM failed: {e}")
            return {"action": "DO_NOTHING", "reasoning": f"LLM unavailable: {e}"}

    # ── Action handlers ───────────────────────────────────────────────────────

    def _handle_trade(self, details: dict):
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
        logger.info(f"PM{self.pm_id} → exec_order: {symbol} {details.get('direction','BUY')}")

    def _handle_research(self, details: dict):
        """Publish research event. Inline research now happens via tool calls in _decide."""
        from common.core.event_bus import get_bus
        get_bus().publish(
            f"research.{self.pm_id}",
            {"question": details.get("question", ""), "priority": details.get("priority", "medium")},
            pm_id=self.pm_id, severity="INFO",
        )

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

            prompt = f"""You are PM{self.pm_id}. Current strategy:
{json.dumps(current, indent=2)}

Hypothesis: {hypothesis}

Propose an updated strategy JSON with fields:
name, description, watchlist (NSE symbols list), pipeline, gates (dict), sizing (dict),
data_sources (list), autonomy (dict: can_short, can_fno, universe).

Return ONLY valid JSON."""
            resp = litellm.completion(
                model="openai/moonshotai/kimi-k2.6",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800, temperature=0.3,
                api_base="https://integrate.api.nvidia.com/v1",
                api_key=__import__("os").getenv("NVIDIA_NIM_API_KEY"),
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            new_strategy = json.loads(raw)

            current_bt = backtest_strategy(current)
            new_bt = backtest_strategy(new_strategy)
            cs, ns = current_bt["sharpe"], new_bt["sharpe"]

            if ns >= cs or current_bt["n_trades"] == 0:
                new_ver = commit_new_version(
                    self.pm_id, new_strategy, parent_version=current_ver,
                    notes=f"{hypothesis[:200]} | sharpe {cs:.2f}→{ns:.2f}",
                )
                logger.info(f"PM{self.pm_id} evolved v{current_ver}→v{new_ver} (sharpe {cs:.2f}→{ns:.2f})")
                from common.core.event_bus import get_bus
                get_bus().publish(f"strategy.update.{self.pm_id}",
                    {"from_version": current_ver, "to_version": new_ver,
                     "hypothesis": hypothesis, "sharpe_before": cs, "sharpe_after": ns},
                    pm_id=self.pm_id, severity="INFO")
            else:
                logger.info(f"PM{self.pm_id} evolution rejected: sharpe {cs:.2f}→{ns:.2f}")
                from common.core.pm_state import append_journal
                append_journal(self.pm_id,
                    f"**EVOLVE rejected** — {hypothesis[:100]} | sharpe {cs:.2f}→{ns:.2f}")
        except Exception as e:
            logger.warning(f"PM{self.pm_id} evolve failed: {e}")

    def _handle_pivot(self, details: dict, state: dict):
        from common.core.pm_state import write_plan
        new_direction = details.get("new_direction", "")
        if new_direction:
            write_plan(self.pm_id,
                f"# PM{self.pm_id} Strategy Plan\n\n## Direction\n{new_direction}\n\n## Status: PIVOTING\n")
            self._handle_research({"question": f"Research for pivot: {new_direction}", "priority": "high"})

    # ── Cycle ─────────────────────────────────────────────────────────────────

    def run_cycle(self, trigger: str = "manual") -> dict:
        ts = datetime.now(IST)
        logger.info(f"PM{self.pm_id} Strategist cycle start [{trigger}]")
        self._emit_thinking("start", f"Starting cycle [{trigger}]")

        state = self._read_state()
        rival = self._read_rival_snapshot()
        decision = self._decide(state, rival, trigger)

        action = decision.get("action", "DO_NOTHING")
        reasoning = decision.get("reasoning", "")
        details = decision.get("details", {})

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

        from common.core.pm_state import append_journal
        rival_summary = ""
        if rival.get("leaderboard"):
            top = rival["leaderboard"][0]
            rival_summary = f" | Leader: PM{top['pm_id']} +₹{top.get('total_pnl', 0):.0f}"
        append_journal(self.pm_id,
            f"**[{trigger}]** Action: `{action}` — {reasoning}{rival_summary}\n"
            + (f"Details: {json.dumps(details)}" if details else ""))

        self._emit_thinking("done", f"{action}: {reasoning}")

        cycle = {"ts": ts.isoformat(), "trigger": trigger, "action": action,
                 "reasoning": reasoning, "details": details}
        with self._cycles_path.open("a") as f:
            f.write(json.dumps(cycle) + "\n")

        logger.info(f"PM{self.pm_id} Strategist cycle done: {action} — {reasoning}")
        return cycle

    def _emit_thinking(self, status: str, context: str):
        try:
            from common.core.event_bus import get_bus
            get_bus().publish(f"agent.thinking.{self.pm_id}",
                {"agent": "strategist", "status": status, "context": context},
                pm_id=self.pm_id)
        except Exception:
            pass

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run_forever(self, off_shift_interval_min: int = 15):
        from common.core.event_bus import get_bus
        from common.core.pm_runtime import get_pm_config

        cfg = get_pm_config(self.pm_id)
        interval_min = cfg.get("off_shift_interval_min", off_shift_interval_min)

        cursor = int(self._cursor_path.read_text().strip()) if self._cursor_path.exists() else get_bus().latest_id()
        logger.info(f"PM{self.pm_id} Strategist started — cursor={cursor}, interval={interval_min}min")

        # Stagger PMs by offsetting when they consider their last interval cycle.
        # PM1: fires at t=0, t=15, t=30...
        # PM2: fires at t=7.5, t=22.5, t=37.5... (half-interval offset)
        stagger_s = (int(self.pm_id) - 1) * (interval_min * 60 / 2)
        last_interval_cycle = time.time() - (interval_min * 60 - stagger_s)
        if stagger_s > 0:
            logger.info(f"PM{self.pm_id} interval offset {stagger_s:.0f}s (fires in {stagger_s:.0f}s)")

        while True:
            try:
                events = get_bus().subscribe(f"pm.wakeup.{self.pm_id}", since_id=cursor)
                for ev in events:
                    cursor = ev["id"]
                    payload = ev.get("payload", {}) or {}
                    base = payload.get("trigger") or payload.get("reason") or "event"
                    shift = payload.get("shift")
                    trigger = f"{base}:{shift}" if shift else str(base)
                    self.run_cycle(trigger)
                self._cursor_path.write_text(str(cursor))

                now = time.time()
                if now - last_interval_cycle >= interval_min * 60:
                    self.run_cycle("interval:research")
                    last_interval_cycle = now

            except Exception as e:
                logger.error(f"PM{self.pm_id} Strategist loop error: {e}")

            time.sleep(5)
