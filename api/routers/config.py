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


# ── Services health cache (avoids blocking uvicorn shutdown) ──────────────────
import threading as _threading

_health_cache: dict = {}
_health_lock = _threading.Lock()
_health_ts: float = 0.0
_HEALTH_TTL = 300  # 5 minutes


def _run_health_checks() -> dict:
    import os, time
    results: dict = {}

    def _check_groq():
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            return {"status": "unconfigured", "fix": "Set GROQ_API_KEY in .env"}
        try:
            import litellm
            t0 = time.time()
            litellm.completion(model="groq/llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "ping"}], max_tokens=3)
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "model": "llama-3.3-70b-versatile"}
        except Exception as e:
            return {"status": "error", "error": str(e), "fix": "Check GROQ_API_KEY at console.groq.com"}

    def _check_openai():
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            return {"status": "unconfigured", "fix": "Set OPENAI_API_KEY in .env (optional)"}
        try:
            import litellm
            t0 = time.time()
            litellm.completion(model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}], max_tokens=3)
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "model": "gpt-4o-mini"}
        except Exception as e:
            return {"status": "error", "error": str(e), "fix": "Check OPENAI_API_KEY at platform.openai.com"}

    def _check_anthropic():
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return {"status": "unconfigured", "fix": "Set ANTHROPIC_API_KEY in .env (optional)"}
        try:
            import litellm
            t0 = time.time()
            litellm.completion(model="anthropic/claude-3-5-sonnet-20241022",
                messages=[{"role": "user", "content": "ping"}], max_tokens=3)
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "model": "claude-3-5-sonnet"}
        except Exception as e:
            return {"status": "error", "error": str(e), "fix": "Check ANTHROPIC_API_KEY at console.anthropic.com"}

    def _check_bedrock():
        key_id = os.getenv("AWS_ACCESS_KEY_ID", "")
        secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        region = os.getenv("AWS_REGION", "us-east-1")
        if not key_id or not secret:
            return {"status": "unconfigured", "fix": "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in .env (optional)"}
        try:
            import litellm
            t0 = time.time()
            litellm.completion(model="bedrock/anthropic.claude-3-haiku-20240307-v1:0",
                messages=[{"role": "user", "content": "ping"}], max_tokens=3,
                aws_access_key_id=key_id, aws_secret_access_key=secret, aws_region_name=region)
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "model": f"claude-3-haiku ({region})"}
        except Exception as e:
            return {"status": "error", "error": str(e), "fix": "Check AWS credentials and Bedrock model access"}

    def _check_yfinance():
        try:
            import yfinance as yf
            t0 = time.time()
            h = yf.Ticker("RELIANCE.NS").history(period="1d")
            if h.empty:
                return {"status": "degraded", "error": "Empty response", "fix": "Yahoo Finance may be rate-limiting"}
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "sample": f"RELIANCE.NS \u20b9{float(h['Close'].iloc[-1]):.2f}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _check_groww():
        api_key = os.getenv("GROWW_API_KEY", "")
        token = os.getenv("GROWW_ACCESS_TOKEN", "")
        secret = os.getenv("GROWW_SECRET", "")
        if not api_key:
            return {"status": "unconfigured", "fix": "Set GROWW_API_KEY in .env"}
        if not token:
            return {"status": "unconfigured", "fix": "Run: python -m core.groww_client to generate token"}
        try:
            from common.core.groww_client import GrowwClient
            t0 = time.time()
            client = GrowwClient(api_key=api_key, secret=secret)
            price = client.get_ltp("RELIANCE")
            if price is None:
                return {"status": "degraded", "error": "LTP returned None", "fix": "Token may be expired"}
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "sample": f"RELIANCE \u20b9{price:.2f}"}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "fix": "Token expired — run: python -m core.groww_client" if "401" in str(e) else "Check credentials"}

    def _check_telegram():
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token:
            return {"status": "unconfigured", "fix": "Create bot via @BotFather, set TELEGRAM_BOT_TOKEN"}
        if not chat_id:
            return {"status": "unconfigured", "fix": "Set TELEGRAM_CHAT_ID in .env"}
        try:
            import requests as _req
            t0 = time.time()
            r = _req.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5)
            if r.status_code != 200:
                return {"status": "error", "error": f"HTTP {r.status_code}", "fix": "TELEGRAM_BOT_TOKEN is invalid"}
            name = r.json().get("result", {}).get("username", "?")
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "bot": f"@{name}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _check_zerodha():
        api_key = os.getenv("ZERODHA_API_KEY", "")
        token = os.getenv("ZERODHA_ACCESS_TOKEN", "")
        try:
            import yaml
            cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
            mode = yaml.safe_load(cfg_path.read_text()).get("trading", {}).get("mode", "paper") if cfg_path.exists() else "paper"
        except Exception:
            mode = "paper"
        if mode != "live":
            return {"status": "not_required", "detail": "trading.mode is not live"}
        if not api_key:
            return {"status": "unconfigured", "fix": "Set ZERODHA_API_KEY in .env"}
        if not token:
            return {"status": "unconfigured", "fix": "Set ZERODHA_ACCESS_TOKEN (expires daily)"}
        try:
            from kiteconnect import KiteConnect
            t0 = time.time()
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(token)
            profile = kite.profile()
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000), "user": profile.get("user_name", "?")}
        except ImportError:
            return {"status": "error", "error": "kiteconnect not installed", "fix": "pip install kiteconnect"}
        except Exception as e:
            return {"status": "error", "error": str(e), "fix": "Token expired — regenerate via Kite login flow"}

    def _check_twitter():
        bearer = os.getenv("TWITTER_BEARER_TOKEN", "")
        if not bearer:
            return {"status": "not_required", "detail": "DiscoveryAgent uses Nitter by default. Twitter API is optional."}
        try:
            import requests as _req
            t0 = time.time()
            r = _req.get("https://api.twitter.com/2/tweets/search/recent?query=NSE&max_results=10",
                headers={"Authorization": f"Bearer {bearer}"}, timeout=5)
            if r.status_code == 200:
                return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000)}
            return {"status": "error", "error": f"HTTP {r.status_code}", "fix": "Check TWITTER_BEARER_TOKEN"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    results["groq"] = _check_groq()
    results["openai"] = _check_openai()
    results["anthropic"] = _check_anthropic()
    results["bedrock"] = _check_bedrock()
    results["yfinance"] = _check_yfinance()
    results["groww"] = _check_groww()
    results["telegram"] = _check_telegram()
    results["zerodha"] = _check_zerodha()
    results["twitter"] = _check_twitter()
    return results


@router.get("/services/health")
def services_health():
    """Return cached service health. Refreshes in background every 5 minutes."""
    global _health_cache, _health_ts
    import time

    now = time.time()
    with _health_lock:
        cache_age = now - _health_ts
        has_cache = bool(_health_cache)

    if not has_cache or cache_age > _HEALTH_TTL:
        def _refresh():
            global _health_cache, _health_ts
            result = _run_health_checks()
            with _health_lock:
                _health_cache = result
                _health_ts = time.time()
        t = _threading.Thread(target=_refresh, daemon=True)
        t.start()
        if not has_cache:
            t.join(timeout=30)

    with _health_lock:
        return dict(_health_cache)
