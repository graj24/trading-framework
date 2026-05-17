"""
Provision a new PM end-to-end:
  1. Create workspace (pm_<id>/state/*)
  2. Register in pm_registry.json
  3. Create sub-agents in Multica (Researcher, Trader, Risk)

Usage:
  python scripts/register_pm.py --pm_id 3 --prompt pm_prompts/PM3_full_prompt.md
  python scripts/register_pm.py --pm_id 1  # re-provision existing PM1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MULTICA_SERVER = os.getenv("MULTICA_SERVER_URL", "http://13.232.42.85:8080")
MULTICA_TOKEN  = os.getenv("MULTICA_TOKEN", "")

SUB_AGENT_ROLES = {
    "Researcher": "You are PM{pm_id}'s Researcher. Your job: gather data, analyse news, sector flows, fundamentals, and any information PM{pm_id} asks for. Write findings to pm_{pm_id}/state/inbox.jsonl and report back.",
    "Trader":     "You are PM{pm_id}'s Trader. Your job: receive exec_order events from the event bus (topic exec_order.{pm_id}), run pre-trade gates, and place orders via the broker abstraction. Never trade without a valid exec_order event.",
    "Risk":       "You are PM{pm_id}'s Risk Officer. Your job: monitor portfolio VaR and P&L continuously. Publish risk.breach.{pm_id} events when limits are breached. Escalate to PM{pm_id} via pm.wakeup.{pm_id}.",
}


def _multica_create_agent(name: str, system_prompt: str) -> dict | None:
    if not MULTICA_TOKEN:
        logger.warning(f"MULTICA_TOKEN not set — skipping Multica agent creation for {name}")
        return None
    try:
        # Discover workspace_id from the API
        ws_resp = requests.get(
            f"{MULTICA_SERVER}/api/workspaces",
            headers={"Authorization": f"Bearer {MULTICA_TOKEN}"},
            timeout=10,
        )
        workspace_id = None
        if ws_resp.status_code == 200:
            workspaces = ws_resp.json()
            if workspaces:
                workspace_id = workspaces[0]["id"]

        payload = {"name": name, "system_prompt": system_prompt, "provider": "kiro"}
        if workspace_id:
            payload["workspace_id"] = workspace_id

        resp = requests.post(
            f"{MULTICA_SERVER}/api/agents",
            json=payload,
            headers={"Authorization": f"Bearer {MULTICA_TOKEN}"},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info(f"  Multica agent created: {name} (id={data.get('id')})")
            return data
        else:
            logger.warning(f"  Multica agent creation failed for {name}: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"  Multica API error for {name}: {e}")
        return None


def register(pm_id: str, prompt_path: str | None):
    from core.pm_runtime import register_pm
    from core.pm_state import write_team

    logger.info(f"=== Registering PM{pm_id} ===")

    # 1. Provision workspace
    ws = register_pm(pm_id, prompt_path=prompt_path)
    logger.info(f"Workspace: {ws}")

    # 2. Create Multica sub-agents
    team = {}
    for role, prompt_template in SUB_AGENT_ROLES.items():
        agent_name = f"PM{pm_id}.{role}"
        system_prompt = prompt_template.format(pm_id=pm_id)
        result = _multica_create_agent(agent_name, system_prompt)
        team[role.lower()] = {
            "role": role,
            "multica_agent": agent_name,
            "multica_id": result.get("id") if result else None,
            "system_prompt_summary": prompt_template[:80],
        }

    write_team(pm_id, team)
    logger.info(f"Team registered: {list(team.keys())}")

    # 3. Print summary
    logger.info(f"\n✅ PM{pm_id} provisioned successfully")
    logger.info(f"   Workspace:  pm_{pm_id}/")
    logger.info(f"   Prompt:     {prompt_path or '(none)'}")
    logger.info(f"   Sub-agents: {', '.join(f'PM{pm_id}.{r}' for r in SUB_AGENT_ROLES)}")
    logger.info(f"\nStart daemons:")
    logger.info(f"   python -m agents.pm_triage --pm_id {pm_id} &")
    logger.info(f"   python -m agents.pm_trader --pm_id {pm_id} &")
    logger.info(f"   python -m agents.pm_risk   --pm_id {pm_id} &")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision a new PM")
    parser.add_argument("--pm_id", required=True, help="PM identifier (e.g. 1, 2, 3)")
    parser.add_argument("--prompt", default=None, help="Path to full prompt .md file")
    args = parser.parse_args()

    # Add project root to path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from dotenv import load_dotenv
    load_dotenv()

    register(args.pm_id, args.prompt)
