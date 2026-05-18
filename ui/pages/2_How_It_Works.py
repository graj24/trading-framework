"""How It Works — interactive visual of the full trading pipeline."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

st.set_page_config(page_title="How It Works · Trading Framework", page_icon="🗺️", layout="wide")

ROOT = Path(__file__).parent.parent.parent

# ── load config + last-run data ───────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_config():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

@st.cache_data(ttl=60)
def _last_signal(symbol: str) -> dict:
    try:
        from core.knowledge_base import read_kb
        import sys; sys.path.insert(0, str(ROOT))
        tech  = read_kb(symbol, "technical_analysis.json")
        news  = read_kb(symbol, "news_history.json")
        pat   = read_kb(symbol, "patterns.json")
        fund  = read_kb(symbol, "fundamentals.json")
        wts   = read_kb(symbol, "signal_weights.json")
        return {"tech": tech, "news": news, "pat": pat, "fund": fund, "wts": wts}
    except Exception:
        return {}

@st.cache_data(ttl=300)
def _sector_returns() -> dict:
    try:
        import yfinance as yf
        tickers = {
            "IT": "^CNXIT", "FMCG": "^CNXFMCG", "Auto": "^CNXAUTO",
            "Energy": "^CNXENERGY", "Pharma": "^CNXPHARMA", "Metal": "^CNXMETAL",
            "Realty": "^CNXREALTY", "Bank": "^NSEBANK", "Infra": "^CNXINFRA",
            "NIFTY": "^NSEI",
        }
        result = {}
        for name, ticker in tickers.items():
            try:
                df = yf.Ticker(ticker).history(period="30d", progress=False)
                if not df.empty:
                    ret = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
                    result[name] = round(float(ret), 2)
            except Exception:
                pass
        return result
    except Exception:
        return {}

@st.cache_data(ttl=60)
def _last_trades(n: int = 5) -> list[dict]:
    db = ROOT / "paper_trades.db"
    if not db.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT symbol, entry_date, outcome, pnl_pct, pnl_inr, reasoning "
            "FROM trades ORDER BY created_at DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        return [{"symbol": r[0], "date": r[1], "outcome": r[2],
                 "pnl_pct": r[3], "pnl_inr": r[4], "reasoning": r[5]} for r in rows]
    except Exception:
        return []

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
body, .stApp { background-color: #0d1117; color: #e6edf3; }

.agent-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 16px 12px;
    text-align: center;
    transition: border-color 0.2s;
}
.agent-card:hover { border-color: #58a6ff; }
.agent-card .icon { font-size: 2rem; }
.agent-card .name { font-weight: 600; font-size: 0.9rem; margin-top: 6px; color: #e6edf3; }
.agent-card .desc { font-size: 0.75rem; color: #8b949e; margin-top: 4px; }
.agent-card .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 0.7rem;
    margin-top: 6px;
    font-weight: 600;
}
.badge-ok   { background: #1a4731; color: #3fb950; }
.badge-warn { background: #3d2b00; color: #d29922; }
.badge-off  { background: #21262d; color: #8b949e; }

.pipeline-step {
    background: #161b22;
    border-left: 3px solid #58a6ff;
    border-radius: 0 8px 8px 0;
    padding: 12px 16px;
    margin-bottom: 8px;
}
.pipeline-step .step-title { font-weight: 600; color: #58a6ff; font-size: 0.9rem; }
.pipeline-step .step-desc  { color: #8b949e; font-size: 0.8rem; margin-top: 4px; }

.decision-card {
    background: #161b22;
    border-radius: 12px;
    padding: 20px;
    border: 1px solid #30363d;
}
.score-bar-wrap { margin: 6px 0; }
.score-label { font-size: 0.8rem; color: #8b949e; width: 120px; display: inline-block; }

.regime-badge {
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 1rem;
    letter-spacing: 0.5px;
}
.regime-bull  { background: #1a4731; color: #3fb950; }
.regime-bear  { background: #3d1515; color: #f85149; }
.regime-range { background: #1c2128; color: #79c0ff; }
.regime-vol   { background: #3d2b00; color: #d29922; }
</style>
""", unsafe_allow_html=True)

# ── header ────────────────────────────────────────────────────────────────────
st.title("🗺️ How It Works")
st.caption("A live view of the trading pipeline — from raw data to executed trade.")

config = _load_config()
watchlist = config.get("watchlist", [])

# ── symbol selector ───────────────────────────────────────────────────────────
col_sym, col_refresh = st.columns([4, 1])
with col_sym:
    symbol = st.selectbox("View pipeline for symbol", watchlist, index=0)
with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

kb = _last_signal(symbol)
tech = kb.get("tech", {})
news = kb.get("news", {})
pat  = kb.get("pat", {})
fund = kb.get("fund", {})

# ── SECTION 1: Agent cards ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("① The 7 Agents")
st.caption("Each agent runs independently and feeds a score into the MasterAgent.")

agents = [
    {
        "icon": "📊", "name": "Technical Agent",
        "desc": "RSI · MACD · EMA · ATR · Volume",
        "score": tech.get("technical_score"),
        "score_max": 10,
        "status": "ok" if tech else "off",
        "detail": f"Score: {tech.get('technical_score','—')}/10 | Trend: {tech.get('trend','—')} | RSI: {tech.get('rsi','—')}",
    },
    {
        "icon": "📰", "name": "News Agent",
        "desc": "FinBERT sentiment · Tier classification",
        "score": None,
        "status": "ok" if news.get("news") else "off",
        "detail": f"{len(news.get('news',[]))} articles | Avg sentiment: {round(sum(n.get('sentiment',0) for n in news.get('news',[])[-5:])/max(len(news.get('news',[])),1),3)}",
    },
    {
        "icon": "🔍", "name": "Pattern Agent",
        "desc": "DTW matching · Expected value",
        "score": pat.get("summary", {}).get("expected_value") if pat else None,
        "score_max": None,
        "status": "ok" if pat else "off",
        "detail": f"EV: {pat.get('summary',{}).get('expected_value','—')}% | Win rate: {pat.get('summary',{}).get('win_rate','—')}%",
    },
    {
        "icon": "🌍", "name": "Regime Agent",
        "desc": "NIFTY + stock-specific regime",
        "score": None,
        "status": "ok",
        "detail": f"Mode: {config['trading']['mode'].upper()}",
    },
    {
        "icon": "🤖", "name": "ML Model (Daily)",
        "desc": "GBM · 5-day forward return",
        "score": None,
        "status": "ok" if (ROOT / "stocks" / "ml_signal_model.pkl").exists() else "warn",
        "detail": "Trained" if (ROOT / "stocks" / "ml_signal_model.pkl").exists() else "Not trained yet",
    },
    {
        "icon": "⚡", "name": "ML Model (1h)",
        "desc": "GBM · 3-hour intraday return",
        "score": None,
        "status": "ok" if (ROOT / "stocks_1h" / "india_intraday_model.pkl").exists() else "warn",
        "detail": "Trained" if (ROOT / "stocks_1h" / "india_intraday_model.pkl").exists() else "Not trained yet",
    },
    {
        "icon": "📅", "name": "Earnings Agent",
        "desc": "NSE/BSE filings · EPS consensus",
        "score": None,
        "status": "ok",
        "detail": "Monitors overnight filings",
    },
]

cols = st.columns(len(agents))
for col, ag in zip(cols, agents):
    badge_cls = {"ok": "badge-ok", "warn": "badge-warn", "off": "badge-off"}[ag["status"]]
    badge_txt = {"ok": "● Active", "warn": "⚠ No model", "off": "○ No data"}[ag["status"]]
    col.markdown(f"""
<div class="agent-card">
  <div class="icon">{ag['icon']}</div>
  <div class="name">{ag['name']}</div>
  <div class="desc">{ag['desc']}</div>
  <span class="badge {badge_cls}">{badge_txt}</span>
  <div class="desc" style="margin-top:8px">{ag['detail']}</div>
</div>
""", unsafe_allow_html=True)

# ── SECTION 2: Interactive pipeline flow ──────────────────────────────────────
st.markdown("---")
st.subheader("② The Decision Pipeline")

left, right = st.columns([3, 2])

with left:
    # Plotly Sankey-style flow diagram
    node_labels = [
        "Price Data", "News", "Patterns", "Regime", "ML Daily", "ML 1h", "Earnings",
        "MasterAgent", "LLM (Groq)", "Rule Fallback",
        "Confidence\nFilter ≥60%", "Trend+MACD\n+Volume Gate",
        "Risk Manager\n(Kelly+ATR)", "ExecutionAgent",
        "paper_trades.db", "LearningAgent",
    ]
    # Node positions (x, y) in 0-1 space
    xs = [0.05]*7 + [0.30, 0.50, 0.50, 0.65, 0.65, 0.78, 0.88, 1.0, 1.0]
    ys = [0.05, 0.20, 0.35, 0.50, 0.65, 0.80, 0.92,
          0.45, 0.30, 0.60, 0.30, 0.60, 0.45, 0.45, 0.35, 0.60]

    node_colors = (
        ["#1f6feb"] * 7 +          # input agents
        ["#388bfd"] +               # MasterAgent
        ["#a371f7", "#d29922"] +    # LLM, Rule fallback
        ["#3fb950", "#3fb950"] +    # filters
        ["#f0883e"] +               # risk
        ["#58a6ff"] +               # execution
        ["#21262d", "#8b949e"]      # DB, learning
    )

    # Edges: (source_idx, target_idx, value, label)
    edges = [
        (0, 7, 3, "tech score"),
        (1, 7, 2, "sentiment"),
        (2, 7, 2, "pattern EV"),
        (3, 7, 2, "regime"),
        (4, 7, 2, "ml_proba"),
        (5, 7, 2, "ml_1h_proba"),
        (6, 7, 1, "earnings signal"),
        (7, 8, 4, "RAG context"),
        (7, 9, 2, "fallback"),
        (8, 10, 3, "decision"),
        (9, 10, 2, "decision"),
        (10, 11, 3, "BUY"),
        (11, 12, 3, "filtered BUY"),
        (12, 13, 3, "sized order"),
        (13, 14, 2, "paper trade"),
        (14, 15, 1, "closed trade"),
        (15, 7, 1, "updated weights"),
    ]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20, thickness=18,
            label=node_labels,
            color=node_colors,
            x=xs, y=ys,
            hovertemplate="%{label}<extra></extra>",
        ),
        link=dict(
            source=[e[0] for e in edges],
            target=[e[1] for e in edges],
            value=[e[2] for e in edges],
            label=[e[3] for e in edges],
            color="rgba(88,166,255,0.15)",
            hovertemplate="%{label}<extra></extra>",
        ),
    ))
    fig.update_layout(
        height=520,
        paper_bgcolor="#0d1117",
        font=dict(color="#e6edf3", size=11),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.markdown("**Pipeline steps**")
    steps = [
        ("① Agents run in parallel", "7 agents analyse the stock independently — technical, news, pattern, regime, 2× ML, earnings."),
        ("② MasterAgent aggregates", "Scores are merged into a composite signal. RAG context (fundamentals, past reactions) is retrieved from the knowledge base."),
        ("③ LLM makes the call", "NVIDIA NIM Kimi K2 receives the full context and returns BUY / HOLD / SKIP with confidence and entry/SL/target."),
        ("④ Rule fallback", "If the LLM is unavailable, a deterministic rule-based decision runs instead. Same filters apply."),
        ("⑤ Confidence filter", "BUY is blocked if confidence < 60%."),
        ("⑥ Trend + MACD + Volume gate", "BUY requires: trend=up, MACD=bullish, volume ≥ 1× avg. Missing values fail-closed."),
        ("⑦ Risk Manager", "Kelly half-sizing, ATR stop-loss, correlation gate, sector overlap check, daily loss limit."),
        ("⑧ ExecutionAgent", "Paper: writes to SQLite. Live: places order via broker API. Shadow: both simultaneously."),
        ("⑨ LearningAgent", "After each closed trade, per-stock signal weights are updated via EMA boost/decay."),
    ]
    for title, desc in steps:
        st.markdown(f"""
<div class="pipeline-step">
  <div class="step-title">{title}</div>
  <div class="step-desc">{desc}</div>
</div>
""", unsafe_allow_html=True)

# ── SECTION 3: Last signal scores for selected symbol ─────────────────────────
st.markdown("---")
st.subheader(f"③ Last Signal Scores — {symbol}")

score_col, regime_col = st.columns([3, 2])

with score_col:
    tech_score = float(tech.get("technical_score", 0) or 0)
    news_list  = news.get("news", [])
    avg_sent   = sum(n.get("sentiment", 0) for n in news_list[-5:]) / max(len(news_list), 1)
    pat_ev     = float((pat.get("summary", {}) or {}).get("expected_value", 0) or 0)
    win_rate   = float((pat.get("summary", {}) or {}).get("win_rate", 50) or 50)

    bars = [
        ("Technical Score", tech_score / 10, f"{tech_score:.1f}/10",
         "#3fb950" if tech_score >= 7 else "#d29922" if tech_score >= 4 else "#f85149"),
        ("News Sentiment",  (avg_sent + 1) / 2, f"{avg_sent:+.3f}",
         "#3fb950" if avg_sent > 0.1 else "#f85149" if avg_sent < -0.1 else "#8b949e"),
        ("Pattern EV",      min(max((pat_ev + 5) / 10, 0), 1), f"{pat_ev:+.2f}%",
         "#3fb950" if pat_ev > 0 else "#f85149"),
        ("Pattern Win Rate", win_rate / 100, f"{win_rate:.0f}%",
         "#3fb950" if win_rate > 55 else "#d29922" if win_rate > 45 else "#f85149"),
    ]

    fig_scores = go.Figure()
    for i, (label, frac, text, color) in enumerate(bars):
        fig_scores.add_trace(go.Bar(
            x=[frac], y=[label], orientation="h",
            marker_color=color, text=text, textposition="inside",
            textfont=dict(color="white", size=13),
            showlegend=False,
            hovertemplate=f"{label}: {text}<extra></extra>",
        ))
        # Background bar
        fig_scores.add_trace(go.Bar(
            x=[1 - frac], y=[label], orientation="h",
            marker_color="#21262d", showlegend=False,
            hoverinfo="skip",
        ))

    fig_scores.update_layout(
        barmode="stack",
        height=200,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(color="#e6edf3"),
        xaxis=dict(showticklabels=False, showgrid=False, range=[0, 1]),
        yaxis=dict(showgrid=False),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig_scores, use_container_width=True)

with regime_col:
    # Regime badge
    regime_map = {
        "trending_bull":   ("trending_bull",  "🐂 Trending Bull",  "regime-bull"),
        "trending_bear":   ("trending_bear",  "🐻 Trending Bear",  "regime-bear"),
        "ranging":         ("ranging",        "↔ Ranging",         "regime-range"),
        "high_volatility": ("high_volatility","⚡ High Volatility", "regime-vol"),
    }
    # Try to read regime from KB
    regime_key = "ranging"
    try:
        import sqlite3
        conn = sqlite3.connect(ROOT / "paper_trades.db")
        # regime isn't stored in trades — show config mode instead
        conn.close()
    except Exception:
        pass

    _, label, cls = regime_map.get(regime_key, ("ranging", "↔ Ranging", "regime-range"))
    st.markdown(f"""
<div style="text-align:center; padding: 20px 0">
  <div style="color:#8b949e; font-size:0.8rem; margin-bottom:8px">MARKET REGIME</div>
  <span class="regime-badge {cls}">{label}</span>
  <div style="color:#8b949e; font-size:0.75rem; margin-top:12px">
    Updates every cycle via RegimeAgent (NIFTY ADX + volatility)
  </div>
</div>
""", unsafe_allow_html=True)

    # Trading mode badge
    mode = config["trading"]["mode"]
    mode_color = {"paper": "#1f6feb", "live": "#3fb950", "shadow": "#d29922"}.get(mode, "#8b949e")
    st.markdown(f"""
<div style="text-align:center; padding: 10px 0">
  <div style="color:#8b949e; font-size:0.8rem; margin-bottom:8px">TRADING MODE</div>
  <span style="background:{mode_color}22; color:{mode_color}; padding:6px 20px;
               border-radius:20px; font-weight:700; font-size:1rem; border:1px solid {mode_color}44">
    {mode.upper()}
  </span>
</div>
""", unsafe_allow_html=True)

# ── SECTION 4: Sector heatmap ─────────────────────────────────────────────────
st.markdown("---")
st.subheader("④ Sector Rotation Heatmap (30-day returns)")
st.caption("Colour = 30-day return. Green = outperforming, Red = underperforming.")

with st.spinner("Fetching sector data..."):
    sector_data = _sector_returns()

if sector_data:
    sectors = list(sector_data.keys())
    returns = list(sector_data.values())
    colors  = ["#3fb950" if r > 0 else "#f85149" for r in returns]

    fig_sector = go.Figure(go.Bar(
        x=sectors, y=returns,
        marker_color=colors,
        text=[f"{r:+.1f}%" for r in returns],
        textposition="outside",
        textfont=dict(color="#e6edf3"),
        hovertemplate="%{x}: %{y:+.2f}%<extra></extra>",
    ))
    fig_sector.add_hline(y=0, line_color="#30363d", line_width=1)
    fig_sector.update_layout(
        height=300,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(color="#e6edf3"),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#21262d", title="30d Return %"),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig_sector, use_container_width=True)
else:
    st.info("Sector data unavailable (network or market closed).")

# ── SECTION 5: Recent trades ──────────────────────────────────────────────────
st.markdown("---")
st.subheader("⑤ Recent Trades")

trades = _last_trades(8)
if trades:
    for t in trades:
        pnl = t.get("pnl_inr") or 0
        pct = t.get("pnl_pct") or 0
        outcome = t.get("outcome", "open")
        color = "#3fb950" if pnl > 0 else "#f85149" if pnl < 0 else "#8b949e"
        icon  = "✅" if pnl > 0 else "❌" if pnl < 0 else "🔵"
        reasoning = (t.get("reasoning") or "")[:80]

        st.markdown(f"""
<div style="background:#161b22; border:1px solid #30363d; border-radius:8px;
            padding:10px 16px; margin-bottom:6px; display:flex; align-items:center; gap:16px">
  <span style="font-size:1.2rem">{icon}</span>
  <span style="font-weight:600; color:#e6edf3; min-width:100px">{t['symbol']}</span>
  <span style="color:#8b949e; font-size:0.8rem; min-width:80px">{str(t.get('date',''))[:10]}</span>
  <span style="color:{color}; font-weight:600; min-width:80px">₹{pnl:+.2f}</span>
  <span style="color:{color}; font-size:0.85rem; min-width:60px">{pct:+.2f}%</span>
  <span style="color:#8b949e; font-size:0.8rem">{reasoning}</span>
</div>
""", unsafe_allow_html=True)
else:
    st.info("No trades yet. Run `python main.py` to generate signals.")

# ── SECTION 6: Architecture summary ──────────────────────────────────────────
st.markdown("---")
st.subheader("⑥ Architecture at a Glance")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Agents", "7", "running in parallel")
c2.metric("ML Models", "2", "daily + intraday")
c3.metric("Risk gates", "5", "Kelly · ATR · corr · sector · daily limit")
c4.metric("Brokers supported", "3", "Zerodha · Upstox · Angel One")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Data sources", "4", "yfinance · Groww · NSE · BSE")
c2.metric("LLM providers", "4", "Groq · OpenAI · Anthropic · Bedrock")
c3.metric("Backtesting", "2 strategies", "Gap · Intraday ML")
c4.metric("Mode", config["trading"]["mode"].upper(),
          f"Capital: ₹{config['trading']['capital']:,}")
