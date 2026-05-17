"""Setup Wizard — configure API keys and validate connections."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Setup · Trading Framework", page_icon="🔧", layout="wide")

ENV_PATH = Path(__file__).parent.parent.parent / ".env"

# ── helpers ───────────────────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
    return vals


def _write_env(vals: dict[str, str]) -> None:
    """Merge new values into .env, preserving comments and order."""
    lines: list[str] = []
    written: set[str] = set()

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in vals:
                    lines.append(f"{k}={vals[k]}")
                    written.add(k)
                    continue
            lines.append(line)

    for k, v in vals.items():
        if k not in written:
            lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(lines) + "\n")


def _mask(v: str) -> str:
    if not v:
        return ""
    return v[:4] + "•" * max(0, len(v) - 8) + v[-4:] if len(v) > 8 else "•" * len(v)


def _test_groq(key: str) -> tuple[bool, str]:
    try:
        import litellm
        r = litellm.completion(
            model="groq/llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            max_tokens=5,
            api_key=key,
        )
        reply = r.choices[0].message.content.strip()
        return True, f"✅ Connected — model replied: \"{reply}\""
    except Exception as e:
        return False, f"❌ {e}"


def _test_telegram(token: str, chat_id: str) -> tuple[bool, str]:
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Trading Framework connected!"},
            timeout=5,
        )
        if r.status_code == 200:
            return True, "✅ Test message sent to your Telegram chat"
        return False, f"❌ HTTP {r.status_code}: {r.text[:100]}"
    except Exception as e:
        return False, f"❌ {e}"


def _test_groww(api_key: str) -> tuple[bool, str]:
    try:
        import requests
        r = requests.get(
            "https://groww.in/v1/api/stocks_data/v1/tr_live_data/exchange/NSE/segment/CASH/RELIANCE/latest",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if r.status_code == 200:
            return True, "✅ Groww API reachable"
        return False, f"⚠️ HTTP {r.status_code} (key may still be valid — Groww auth is session-based)"
    except Exception as e:
        return False, f"❌ {e}"


# ── page ──────────────────────────────────────────────────────────────────────

st.title("🔧 Setup")
st.caption("Configure your API keys. Values are saved to `.env` in the project root.")

env = _read_env()

# ── Section 1: Required ───────────────────────────────────────────────────────
st.markdown("---")
st.subheader("1 · Required — LLM")

col1, col2 = st.columns([3, 1])
with col1:
    groq_key = st.text_input(
        "Groq API Key",
        value=env.get("GROQ_API_KEY", ""),
        type="password",
        placeholder="gsk_...",
        help="Free at console.groq.com — no credit card needed",
    )
with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    test_groq = st.button("Test connection", key="test_groq")

if test_groq and groq_key:
    with st.spinner("Testing Groq..."):
        ok, msg = _test_groq(groq_key)
    st.success(msg) if ok else st.error(msg)

if groq_key:
    st.caption(f"Stored: `{_mask(groq_key)}`")
else:
    st.warning("⚠️ GROQ_API_KEY is required. Get one free at [console.groq.com](https://console.groq.com).")

# ── Section 2: Recommended ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("2 · Recommended — Live prices & alerts")

with st.expander("📊 Groww API (live LTP / quotes)", expanded=bool(env.get("GROWW_API_KEY"))):
    c1, c2 = st.columns(2)
    groww_key    = c1.text_input("API Key",    value=env.get("GROWW_API_KEY", ""),    type="password")
    groww_secret = c2.text_input("Secret",     value=env.get("GROWW_SECRET", ""),     type="password")
    groww_totp   = c1.text_input("TOTP Secret",value=env.get("GROWW_TOTP_SECRET",""), type="password",
                                  help="Base32 secret from your authenticator app")
    groww_token  = c2.text_input("Access Token (refresh daily)",
                                  value=env.get("GROWW_ACCESS_TOKEN",""), type="password")
    if st.button("Test Groww", key="test_groww") and groww_key:
        with st.spinner("Testing..."):
            ok, msg = _test_groww(groww_key)
        st.success(msg) if ok else st.warning(msg)
    st.caption("Get keys at [groww.in/user/profile/trading-apis](https://groww.in/user/profile/trading-apis)")

with st.expander("📬 Telegram Alerts", expanded=bool(env.get("TELEGRAM_BOT_TOKEN"))):
    c1, c2 = st.columns(2)
    tg_token   = c1.text_input("Bot Token",  value=env.get("TELEGRAM_BOT_TOKEN",""), type="password",
                                 help="From @BotFather on Telegram")
    tg_chat_id = c2.text_input("Chat ID",    value=env.get("TELEGRAM_CHAT_ID",""),
                                 help="From api.telegram.org/bot<TOKEN>/getUpdates")
    if st.button("Send test message", key="test_tg") and tg_token and tg_chat_id:
        with st.spinner("Sending..."):
            ok, msg = _test_telegram(tg_token, tg_chat_id)
        st.success(msg) if ok else st.error(msg)

# ── Section 3: Live trading ───────────────────────────────────────────────────
st.markdown("---")
st.subheader("3 · Live trading (optional)")

broker_choice = st.radio("Broker", ["None (paper trading)", "Zerodha", "Upstox", "Angel One"],
                          horizontal=True)

if broker_choice == "Zerodha":
    with st.expander("Zerodha Kite", expanded=True):
        c1, c2 = st.columns(2)
        z_key    = c1.text_input("API Key",      value=env.get("ZERODHA_API_KEY",""),    type="password")
        z_secret = c2.text_input("API Secret",   value=env.get("ZERODHA_API_SECRET",""), type="password")
        z_token  = c1.text_input("Access Token (refresh daily)",
                                  value=env.get("ZERODHA_ACCESS_TOKEN",""), type="password")
        st.caption("Get keys at [kite.trade](https://kite.trade/docs/connect/v3/install/)")
elif broker_choice == "Upstox":
    with st.expander("Upstox", expanded=True):
        c1, c2 = st.columns(2)
        u_key   = c1.text_input("API Key",      value=env.get("UPSTOX_API_KEY",""),    type="password")
        u_token = c2.text_input("Access Token", value=env.get("UPSTOX_ACCESS_TOKEN",""), type="password")
        st.caption("Get keys at [developer.upstox.com](https://developer.upstox.com)")
elif broker_choice == "Angel One":
    with st.expander("Angel One SmartAPI", expanded=True):
        c1, c2 = st.columns(2)
        a_key    = c1.text_input("API Key",     value=env.get("ANGELONE_API_KEY",""),    type="password")
        a_client = c2.text_input("Client ID",   value=env.get("ANGELONE_CLIENT_ID",""))
        a_pass   = c1.text_input("Password",    value=env.get("ANGELONE_PASSWORD",""),   type="password")
        a_totp   = c2.text_input("TOTP Secret", value=env.get("ANGELONE_TOTP_SECRET",""), type="password")
        st.caption("Get keys at [smartapi.angelbroking.com](https://smartapi.angelbroking.com)")

# ── Section 4: Alternative LLM ───────────────────────────────────────────────
st.markdown("---")
with st.expander("4 · Alternative LLM providers (optional)"):
    st.caption("Leave blank to use Groq (default). Fill in only if you want a different provider.")
    c1, c2 = st.columns(2)
    oai_key  = c1.text_input("OpenAI API Key",    value=env.get("OPENAI_API_KEY",""),    type="password")
    ant_key  = c2.text_input("Anthropic API Key", value=env.get("ANTHROPIC_API_KEY",""), type="password")
    aws_id   = c1.text_input("AWS Access Key ID", value=env.get("AWS_ACCESS_KEY_ID",""), type="password")
    aws_sec  = c2.text_input("AWS Secret Key",    value=env.get("AWS_SECRET_ACCESS_KEY",""), type="password")
    aws_reg  = c1.text_input("AWS Region",        value=env.get("AWS_REGION","us-east-1"))

# ── Save ──────────────────────────────────────────────────────────────────────
st.markdown("---")
col_save, col_status = st.columns([1, 3])
with col_save:
    if st.button("💾 Save all keys", type="primary", use_container_width=True):
        new_vals: dict[str, str] = {}

        if groq_key:       new_vals["GROQ_API_KEY"] = groq_key
        if groww_key:      new_vals["GROWW_API_KEY"] = groww_key
        if groww_secret:   new_vals["GROWW_SECRET"] = groww_secret
        if groww_totp:     new_vals["GROWW_TOTP_SECRET"] = groww_totp
        if groww_token:    new_vals["GROWW_ACCESS_TOKEN"] = groww_token
        if tg_token:       new_vals["TELEGRAM_BOT_TOKEN"] = tg_token
        if tg_chat_id:     new_vals["TELEGRAM_CHAT_ID"] = tg_chat_id

        if broker_choice == "Zerodha":
            if z_key:    new_vals["ZERODHA_API_KEY"] = z_key
            if z_secret: new_vals["ZERODHA_API_SECRET"] = z_secret
            if z_token:  new_vals["ZERODHA_ACCESS_TOKEN"] = z_token
        elif broker_choice == "Upstox":
            if u_key:    new_vals["UPSTOX_API_KEY"] = u_key
            if u_token:  new_vals["UPSTOX_ACCESS_TOKEN"] = u_token
        elif broker_choice == "Angel One":
            if a_key:    new_vals["ANGELONE_API_KEY"] = a_key
            if a_client: new_vals["ANGELONE_CLIENT_ID"] = a_client
            if a_pass:   new_vals["ANGELONE_PASSWORD"] = a_pass
            if a_totp:   new_vals["ANGELONE_TOTP_SECRET"] = a_totp

        if oai_key:  new_vals["OPENAI_API_KEY"] = oai_key
        if ant_key:  new_vals["ANTHROPIC_API_KEY"] = ant_key
        if aws_id:   new_vals["AWS_ACCESS_KEY_ID"] = aws_id
        if aws_sec:  new_vals["AWS_SECRET_ACCESS_KEY"] = aws_sec
        if aws_reg:  new_vals["AWS_REGION"] = aws_reg

        _write_env(new_vals)
        st.session_state["saved"] = True

with col_status:
    if st.session_state.get("saved"):
        st.success(f"✅ Saved to `{ENV_PATH}`")

# ── Status summary ────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Current status")

env_now = _read_env()
checks = [
    ("GROQ_API_KEY",         "LLM (required)",          True),
    ("GROWW_API_KEY",        "Groww live prices",        False),
    ("TELEGRAM_BOT_TOKEN",   "Telegram alerts",          False),
    ("ZERODHA_API_KEY",      "Zerodha live trading",     False),
    ("UPSTOX_API_KEY",       "Upstox live trading",      False),
    ("ANGELONE_API_KEY",     "Angel One live trading",   False),
]

cols = st.columns(3)
for i, (key, label, required) in enumerate(checks):
    val = env_now.get(key, "")
    if val:
        cols[i % 3].success(f"✅ {label}")
    elif required:
        cols[i % 3].error(f"❌ {label} — required")
    else:
        cols[i % 3].info(f"⬜ {label} — optional")
