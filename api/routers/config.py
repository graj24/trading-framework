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


@router.get("/services/health")
def services_health():
    """Check health of every external service. Returns status + detail per service."""
    import os, time

    results: dict = {}

    # ── Groq ──────────────────────────────────────────────────────────────────
    def _check_groq():
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            return {"status": "unconfigured", "fix": "Set GROQ_API_KEY in .env"}
        try:
            import litellm
            t0 = time.time()
            r = litellm.completion(
                model="groq/llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=3,
            )
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "model": "llama-3.3-70b-versatile"}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "fix": "Check GROQ_API_KEY is valid at console.groq.com"}

    # ── Yahoo Finance ─────────────────────────────────────────────────────────
    def _check_yfinance():
        try:
            import yfinance as yf
            t0 = time.time()
            h = yf.Ticker("RELIANCE.NS").history(period="1d")
            if h.empty:
                return {"status": "degraded", "error": "Empty response for RELIANCE.NS",
                        "fix": "Yahoo Finance may be rate-limiting. Try again in a few minutes."}
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "sample": f"RELIANCE.NS close ₹{float(h['Close'].iloc[-1]):.2f}"}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "fix": "yfinance may be blocked or rate-limited. No credentials needed."}

    # ── Groww ─────────────────────────────────────────────────────────────────
    def _check_groww():
        api_key = os.getenv("GROWW_API_KEY", "")
        secret  = os.getenv("GROWW_SECRET", "")
        token   = os.getenv("GROWW_ACCESS_TOKEN", "")
        if not api_key:
            return {"status": "unconfigured",
                    "fix": "Set GROWW_API_KEY + GROWW_SECRET in .env. Apply at groww.in/user/profile/trading-apis"}
        if not token:
            return {"status": "unconfigured",
                    "fix": "Set GROWW_ACCESS_TOKEN in .env. Run: python -m core.groww_client to generate one."}
        try:
            from common.core.groww_client import GrowwClient
            t0 = time.time()
            client = GrowwClient(api_key=api_key, secret=secret)
            price = client.get_ltp("RELIANCE")
            if price is None:
                return {"status": "degraded", "error": "LTP returned None for RELIANCE",
                        "fix": "Access token may be expired. Run: python -m core.groww_client to refresh."}
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "sample": f"RELIANCE LTP ₹{price:.2f}"}
        except Exception as e:
            err = str(e)
            fix = "Access token expired — run: python -m core.groww_client" \
                if "401" in err or "token" in err.lower() else \
                "Check GROWW_API_KEY and GROWW_SECRET are correct"
            return {"status": "error", "error": err, "fix": fix}

    # ── Telegram ──────────────────────────────────────────────────────────────
    def _check_telegram():
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id   = os.getenv("TELEGRAM_CHAT_ID", "")
        if not bot_token:
            return {"status": "unconfigured",
                    "fix": "Create a bot via @BotFather on Telegram, set TELEGRAM_BOT_TOKEN in .env"}
        if not chat_id:
            return {"status": "unconfigured",
                    "fix": "Set TELEGRAM_CHAT_ID in .env. Get it from api.telegram.org/bot<TOKEN>/getUpdates"}
        try:
            import requests as _req
            t0 = time.time()
            r = _req.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5)
            if r.status_code != 200:
                return {"status": "error", "error": f"HTTP {r.status_code}",
                        "fix": "TELEGRAM_BOT_TOKEN is invalid. Re-create bot via @BotFather."}
            name = r.json().get("result", {}).get("username", "?")
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "bot": f"@{name}"}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "fix": "Network error reaching api.telegram.org"}

    # ── Zerodha ───────────────────────────────────────────────────────────────
    def _check_zerodha():
        api_key = os.getenv("ZERODHA_API_KEY", "")
        token   = os.getenv("ZERODHA_ACCESS_TOKEN", "")
        mode    = ""
        try:
            import yaml
            cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
            if cfg_path.exists():
                mode = yaml.safe_load(cfg_path.read_text()).get("trading", {}).get("mode", "paper")
        except Exception:
            pass
        if mode != "live":
            return {"status": "not_required",
                    "detail": "trading.mode is not 'live' — Zerodha is unused in paper mode"}
        if not api_key:
            return {"status": "unconfigured",
                    "fix": "Set ZERODHA_API_KEY + ZERODHA_API_SECRET in .env. Get from kite.trade"}
        if not token:
            return {"status": "unconfigured",
                    "fix": "Set ZERODHA_ACCESS_TOKEN in .env. Tokens expire daily — regenerate via Kite login flow."}
        try:
            from kiteconnect import KiteConnect
            t0 = time.time()
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(token)
            profile = kite.profile()
            return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000),
                    "user": profile.get("user_name", "?")}
        except ImportError:
            return {"status": "error", "error": "kiteconnect not installed",
                    "fix": "pip install kiteconnect"}
        except Exception as e:
            err = str(e)
            fix = "Access token expired — regenerate via Kite login flow" \
                if "token" in err.lower() or "403" in err else "Check ZERODHA_API_KEY is correct"
            return {"status": "error", "error": err, "fix": fix}

    # ── Twitter / Nitter ──────────────────────────────────────────────────────
    def _check_twitter():
        bearer = os.getenv("TWITTER_BEARER_TOKEN", "")
        if not bearer:
            return {"status": "not_required",
                    "detail": "DiscoveryAgent uses Nitter scraping by default. Twitter API keys are optional."}
        try:
            import requests as _req
            t0 = time.time()
            r = _req.get(
                "https://api.twitter.com/2/tweets/search/recent?query=NSE&max_results=10",
                headers={"Authorization": f"Bearer {bearer}"}, timeout=5,
            )
            if r.status_code == 200:
                return {"status": "ok", "latency_ms": round((time.time() - t0) * 1000)}
            return {"status": "error", "error": f"HTTP {r.status_code}",
                    "fix": "Check TWITTER_BEARER_TOKEN is valid at developer.twitter.com"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    results["groq"]       = _check_groq()
    results["yfinance"]   = _check_yfinance()
    results["groww"]      = _check_groww()
    results["telegram"]   = _check_telegram()
    results["zerodha"]    = _check_zerodha()
    results["twitter"]    = _check_twitter()
    return results
