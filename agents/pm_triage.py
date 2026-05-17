"""
PM Triage Daemon — per-PM standing process.

Listens to the event bus, applies a rule-based fast path to drop noise,
escalates ambiguous events to a cheap LLM, and routes actionable events to:
  - exec_order.<pm_id>   → PM Trader daemon
  - pm.wakeup.<pm_id>    → PM strategic agent (Multica)
  - research.<pm_id>     → PM Researcher

Run one instance per PM:
  python -m agents.pm_triage --pm_id 1
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from core.event_bus import get_bus
from core.pm_state import push_inbox
from core.pm_runtime import get_pm_config

logger = logging.getLogger(__name__)

# Topics Triage listens to
WATCHED_TOPICS = [
    "price.spike.*",
    "news.*",
    "fill.*",
    "risk.breach.*",
]

# Rule-based fast-path: drop events matching these conditions without LLM
def _is_noise(event: dict, pm_id: str) -> bool:
    payload = event.get("payload", {})
    topic = event.get("topic", "")

    # Ignore fills from other PMs
    if topic.startswith("fill.") and not topic.endswith(f".{pm_id}"):
        return True

    # Ignore risk breaches for other PMs
    if topic.startswith("risk.breach.") and not topic.endswith(f".{pm_id}"):
        return True

    # Ignore tiny price moves (< 0.5%)
    if topic.startswith("price.spike."):
        pct = abs(payload.get("pct", 0))
        if pct < 0.5:
            return True

    return False


def _log_decision(pm_id: str, topic: str, payload: dict, classification: str):
    log_path = Path(f"pm_{pm_id}/state/triage_decisions.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": datetime.utcnow().isoformat(),
        "topic": topic,
        "symbol": payload.get("symbol", ""),
        "severity": payload.get("severity", ""),
        "classification": classification,
    })
    with log_path.open("a") as f:
        f.write(entry + "\n")


def _classify_with_llm(event: dict, pm_id: str) -> str:
    """
    Cheap LLM call to classify event.
    Returns one of: 'ignore', 'exec', 'wakeup', 'research'
    """
    try:
        import litellm
        topic = event.get("topic", "")
        payload = event.get("payload", {})

        # Emit "thinking" start event so UI can show animation
        try:
            get_bus().publish(
                f"agent.thinking.{pm_id}",
                {"agent": "triage", "status": "start", "context": f"Evaluating {topic}: {payload.get('symbol', '')}"},
                pm_id=pm_id,
            )
        except Exception:
            pass

        prompt = (
            f"You are a trading triage assistant for PM{pm_id}.\n"
            f"Event topic: {topic}\n"
            f"Payload: {payload}\n\n"
            "Classify this event as exactly one of:\n"
            "- ignore: not actionable\n"
            "- exec: execute a trade immediately (clear signal)\n"
            "- wakeup: wake the PM for strategic decision\n"
            "- research: queue for deeper research\n\n"
            "Reply with only the single word classification."
        )
        resp = litellm.completion(
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
        )
        result = resp.choices[0].message.content.strip().lower()

        # Emit "thinking" done event
        try:
            get_bus().publish(
                f"agent.thinking.{pm_id}",
                {"agent": "triage", "status": "done", "output": result, "context": f"{topic}: {payload.get('symbol', '')}"},
                pm_id=pm_id,
            )
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning(f"Triage LLM failed: {e} — defaulting to wakeup")
        return "wakeup"


def _route(classification: str, event: dict, pm_id: str):
    bus = get_bus()
    topic = event.get("topic", "")
    payload = event.get("payload", {})

    # Log every classification decision for the UI
    _log_decision(pm_id, topic, payload, classification)

    # If PM is paused, don't route exec orders (but still log and push inbox)
    pause_path = Path(f"pm_{pm_id}/state/PAUSED")
    if pause_path.exists() and classification == "exec":
        logger.info(f"PM{pm_id} paused — exec_order suppressed for {topic}")
        push_inbox(pm_id, {"source": topic, "payload": payload, "suppressed": "paused", "ts": datetime.utcnow().isoformat()})
        return

    if classification == "ignore":
        return

    # Always push to PM inbox for context
    push_inbox(pm_id, {"source": topic, "payload": payload, "ts": datetime.utcnow().isoformat()})

    if classification == "exec":
        bus.publish(f"exec_order.{pm_id}", payload, pm_id=pm_id, severity="HIGH")
        logger.info(f"PM{pm_id} Triage → exec_order: {topic}")

    elif classification == "wakeup":
        bus.publish(f"pm.wakeup.{pm_id}", {"trigger": topic, "payload": payload}, pm_id=pm_id, severity="HIGH")
        logger.info(f"PM{pm_id} Triage → wakeup: {topic}")

    elif classification == "research":
        bus.publish(f"research.{pm_id}", {"trigger": topic, "payload": payload}, pm_id=pm_id, severity="INFO")
        logger.info(f"PM{pm_id} Triage → research: {topic}")


def run(pm_id: str):
    cfg = get_pm_config(pm_id)
    poll_interval = cfg.get("triage_poll_interval_sec", 5)
    bus = get_bus()

    # Persist cursor so restarts don't skip events
    cursor_path = Path(f"pm_{pm_id}/state/triage_cursor.txt")
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    if cursor_path.exists():
        cursor = int(cursor_path.read_text().strip() or 0)
        logger.info(f"PM{pm_id} Triage daemon started — resuming from event id={cursor}")
    else:
        cursor = bus.latest_id()
        logger.info(f"PM{pm_id} Triage daemon started fresh — cursor={cursor}")

    # Log to daemon-specific file as well
    fh = logging.FileHandler(f"logs/pm{pm_id}_triage.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    get_bus().publish(f"system.daemon.{pm_id}", {"daemon": "triage", "event": "start", "cursor": cursor}, pm_id=pm_id)

    while True:
        try:
            for pattern in WATCHED_TOPICS:
                events = bus.subscribe(pattern, since_id=cursor)
                for event in events:
                    cursor = max(cursor, event["id"])
                    if _is_noise(event, pm_id):
                        continue
                    classification = _classify_with_llm(event, pm_id)
                    _route(classification, event, pm_id)
            cursor_path.write_text(str(cursor))
        except Exception as e:
            logger.error(f"PM{pm_id} Triage error: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pm_id", required=True)
    args = parser.parse_args()
    run(args.pm_id)
