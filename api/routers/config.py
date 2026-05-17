from __future__ import annotations
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
import yaml

router = APIRouter(prefix="/api", tags=["config"])

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"
ENV_PATH = Path(__file__).parent.parent.parent / ".env"

SENSITIVE_KEYS = {"GROQ_API_KEY", "GROWW_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN",
                  "ZERODHA_API_KEY", "ZERODHA_ACCESS_TOKEN"}


@router.get("/config")
def get_config():
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "config.yaml not found")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


@router.patch("/config")
def patch_config(updates: dict):
    if not CONFIG_PATH.exists():
        raise HTTPException(404, "config.yaml not found")
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.update(updates)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    from core.config import reload_config
    reload_config()
    return {"status": "ok"}


@router.get("/env/status")
def env_status():
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            is_set = bool(val.strip())
            result[key] = "***set***" if (is_set and key in SENSITIVE_KEYS) else (val.strip() if is_set else "")
    return result


@router.patch("/env")
def patch_env(updates: dict):
    lines = []
    existing: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
            lines.append(line)

    for k, v in updates.items():
        if k in existing:
            lines = [f"{k}={v}" if l.strip().startswith(k + "=") else l for l in lines]
        else:
            lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    return {"status": "ok", "updated": list(updates.keys())}


@router.post("/env/test/{service}")
def test_service(service: str):
    service = service.lower()
    try:
        if service == "groq":
            import litellm
            r = litellm.completion(
                model="groq/llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return {"status": "ok", "response": r.choices[0].message.content}
        elif service == "market":
            import yfinance as yf
            t = yf.Ticker("RELIANCE.NS")
            h = t.history(period="1d")
            price = float(h["Close"].iloc[-1]) if not h.empty else None
            return {"status": "ok", "reliance_ltp": price}
        elif service == "telegram":
            from core.alerts import send_alert
            send_alert("🔔 Test message from Bloomberg UI setup")
            return {"status": "ok"}
        else:
            return {"status": "unknown_service"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
