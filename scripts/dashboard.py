"""
Trading Framework Dashboard
Run: streamlit run dashboard.py
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from core.knowledge_base import kb_path, read_kb
from core.costs import (
    SLIPPAGE_FRAC,
    BROKERAGE_FRAC,
    ROUND_TRIP_COST_FRAC,
)

# ── Config ────────────────────────────────────────────────────────────────────
with open("config.yaml") as f:
    config = yaml.safe_load(f)

CAPITAL   = config["trading"]["capital"]
WATCHLIST = config["watchlist"]
DB_PATH   = Path("paper_trades.db")

st.set_page_config(page_title="Trading Framework", layout="wide", page_icon="📈")

# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def get_ltp(symbol: str) -> float:
    # Handle ticker name differences (underscore → hyphen for NSE)
    ticker = symbol.replace("_", "-") + ".NS"
    try:
        t = yf.Ticker(ticker)
        h = t.history(period="1d")
        return float(h["Close"].iloc[-1]) if not h.empty else 0.0
    except Exception:
        return 0.0

@st.cache_data(ttl=60)
def get_price_history(symbol: str) -> pd.DataFrame:
    path = kb_path(symbol) / "price_history.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    return df

def get_trades() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM trades ORDER BY created_at DESC", conn)
    conn.close()
    return df

def pnl(entry, ltp, size):
    # Round-trip cost (slippage both sides + brokerage both sides + STT on sell)
    # expressed in percentage points. Was a lump-sum 0.06; now sourced from
    # the canonical core.costs constants.
    pct = (ltp - entry) / entry * 100 - ROUND_TRIP_COST_FRAC * 100
    inr = size * pct / 100
    return round(pct, 2), round(inr, 2)

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("📈 Trading Framework")
st.sidebar.caption(f"Mode: {config['trading']['mode'].upper()} | Capital: ₹{CAPITAL:,}")
st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["💼 Portfolio", "🎯 Signals", "📊 Backtest", "📰 News", "⚡ Intraday ML"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Portfolio")
    trades_df = get_trades()

    if trades_df.empty:
        st.info("No trades yet. Run `python3 main.py` to generate signals.")
    else:
        open_trades  = trades_df[trades_df["outcome"] == "open"]
        closed_trades = trades_df[trades_df["outcome"] != "open"]

        # ── Open Positions ────────────────────────────────────────────────────
        st.subheader(f"Open Positions ({len(open_trades)})")
        if not open_trades.empty:
            rows = []
            total_unreal = 0
            for _, t in open_trades.iterrows():
                ltp = get_ltp(t["symbol"])
                pct, inr = pnl(t["entry_price"], ltp, t["position_size"]) if ltp else (0, 0)
                total_unreal += inr
                sl_dist  = round((ltp - t["stop_loss"]) / ltp * 100, 2) if ltp else 0
                tgt_dist = round((t["target"] - ltp) / ltp * 100, 2) if ltp else 0
                rows.append({
                    "Symbol":    t["symbol"],
                    "Entry ₹":   t["entry_price"],
                    "LTP ₹":     ltp,
                    "SL ₹":      t["stop_loss"],
                    "Target ₹":  t["target"],
                    "Size ₹":    t["position_size"],
                    "P&L ₹":     inr,
                    "P&L %":     pct,
                    "SL dist %": sl_dist,
                    "Tgt dist %": tgt_dist,
                })

            pos_df = pd.DataFrame(rows)
            # Ensure numeric columns are actually numeric before formatting
            for col in ["Entry ₹","LTP ₹","SL ₹","Target ₹","Size ₹","P&L ₹","P&L %","SL dist %","Tgt dist %"]:
                pos_df[col] = pd.to_numeric(pos_df[col], errors="coerce")
            st.dataframe(
                pos_df.style
                    .format({"Entry ₹": "₹{:.2f}", "LTP ₹": "₹{:.2f}", "SL ₹": "₹{:.2f}",
                             "Target ₹": "₹{:.2f}", "Size ₹": "₹{:.0f}",
                             "P&L ₹": "₹{:+.2f}", "P&L %": "{:+.2f}%",
                             "SL dist %": "{:.2f}%", "Tgt dist %": "{:.2f}%"}, na_rep="—")
                    .map(lambda v: "color: green" if isinstance(v, (int, float)) and v > 0
                         else ("color: red" if isinstance(v, (int, float)) and v < 0 else ""),
                         subset=["P&L ₹", "P&L %"]),
                use_container_width=True
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Unrealised P&L", f"₹{total_unreal:+.2f}")
            col2.metric("Capital Deployed", f"₹{open_trades['position_size'].sum():,.0f}",
                        f"{open_trades['position_size'].sum()/CAPITAL*100:.1f}% of capital")
            col3.metric("Return on Capital", f"{total_unreal/CAPITAL*100:+.3f}%")

            # Price chart for each open position
            st.subheader("Price Charts")
            cols = st.columns(min(len(open_trades), 2))
            for i, (_, t) in enumerate(open_trades.iterrows()):
                df = get_price_history(t["symbol"])
                if df.empty:
                    continue
                df_plot = df.tail(60)
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df_plot.index, open=df_plot["Open"], high=df_plot["High"],
                    low=df_plot["Low"], close=df_plot["Close"], name=t["symbol"]
                ))
                fig.add_hline(y=t["entry_price"], line_color="blue",  line_dash="dash", annotation_text="Entry")
                fig.add_hline(y=t["stop_loss"],   line_color="red",   line_dash="dash", annotation_text="SL")
                fig.add_hline(y=t["target"],      line_color="green", line_dash="dash", annotation_text="Target")
                fig.update_layout(title=t["symbol"], height=350, xaxis_rangeslider_visible=False,
                                  margin=dict(l=0, r=0, t=30, b=0))
                cols[i % 2].plotly_chart(fig, use_container_width=True)
        else:
            st.info("No open positions.")

        # ── Closed Trades ─────────────────────────────────────────────────────
        st.subheader(f"Closed Trades ({len(closed_trades)})")
        if not closed_trades.empty:
            wins = closed_trades[closed_trades["pnl_inr"] > 0]
            total_real = closed_trades["pnl_inr"].sum()
            win_rate   = len(wins) / len(closed_trades) * 100

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Realised P&L", f"₹{total_real:+,.2f}")
            col2.metric("Win Rate", f"{win_rate:.0f}%", f"{len(wins)}W / {len(closed_trades)-len(wins)}L")
            col3.metric("Avg Win",  f"₹{closed_trades[closed_trades['pnl_inr']>0]['pnl_inr'].mean():+.2f}" if len(wins) else "—")
            col4.metric("Avg Loss", f"₹{closed_trades[closed_trades['pnl_inr']<=0]['pnl_inr'].mean():+.2f}" if len(closed_trades)-len(wins) else "—")

            st.dataframe(
                closed_trades[["symbol","entry_date","exit_date","entry_price","exit_price",
                               "pnl_pct","pnl_inr","outcome","reasoning"]]
                .style.format({"entry_price": "₹{:.2f}", "exit_price": "₹{:.2f}",
                               "pnl_pct": "{:+.2f}%", "pnl_inr": "₹{:+.2f}"}, na_rep="—")
                .map(lambda v: "color: green" if isinstance(v,(int,float)) and v > 0
                     else ("color: red" if isinstance(v,(int,float)) and v < 0 else ""),
                     subset=["pnl_pct","pnl_inr"]),
                use_container_width=True
            )

            # Cumulative P&L chart
            cum = closed_trades.sort_values("exit_date")["pnl_inr"].cumsum()
            fig = go.Figure(go.Scatter(x=list(range(len(cum))), y=cum.values,
                                       fill="tozeroy", line_color="green" if cum.iloc[-1] > 0 else "red"))
            fig.update_layout(title="Cumulative Realised P&L", height=250,
                              yaxis_title="₹", xaxis_title="Trade #",
                              margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No closed trades yet.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SIGNALS
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("Today's Signals")
    st.caption("Run `python3 main.py` to refresh signals. Scores shown from last run.")

    signal_rows = []
    for symbol in WATCHLIST:
        fund  = read_kb(symbol, "fundamentals.json")
        news  = read_kb(symbol, "news_history.json")
        pat   = read_kb(symbol, "patterns.json")
        wts   = read_kb(symbol, "signal_weights.json")

        ltp = get_ltp(symbol)
        sentiments = [n.get("sentiment", 0) for n in news.get("news", [])[-5:]]
        avg_sent = round(sum(sentiments)/len(sentiments), 3) if sentiments else 0

        pat_summary = pat.get("summary", {}) if pat else {}
        signal_rows.append({
            "Symbol":      symbol,
            "LTP ₹":       ltp,
            "Sector":      fund.get("sector", "—"),
            "PE":          fund.get("pe_ratio", "—"),
            "Sentiment":   avg_sent,
            "Win Rate %":  pat_summary.get("win_rate", "—"),
            "Pattern EV %": pat_summary.get("expected_value", "—"),
            "News count":  len(news.get("news", [])),
        })

    sig_df = pd.DataFrame(signal_rows)
    for col in ["LTP ₹","PE","Sentiment","Win Rate %","Pattern EV %"]:
        sig_df[col] = pd.to_numeric(sig_df[col], errors="coerce")
    st.dataframe(
        sig_df.style
            .format({"LTP ₹": "₹{:.2f}", "PE": "{:.1f}", "Sentiment": "{:+.3f}",
                     "Win Rate %": "{:.1f}", "Pattern EV %": "{:.2f}"}, na_rep="—")
            .background_gradient(subset=["Sentiment"], cmap="RdYlGn", vmin=-1, vmax=1)
            .background_gradient(subset=["Pattern EV %"], cmap="RdYlGn", vmin=-5, vmax=5),
        use_container_width=True
    )

    # Signal weights per stock
    st.subheader("Learned Signal Weights")
    st.caption("Updated by LearningAgent after each closed trade. Starts at 1.0.")
    weight_rows = []
    for symbol in WATCHLIST:
        w = read_kb(symbol, "signal_weights.json")
        if w:
            weight_rows.append({"Symbol": symbol, **{k: v for k, v in w.items()
                                                      if k not in ("updated_at",)}})
    if weight_rows:
        st.dataframe(pd.DataFrame(weight_rows).set_index("Symbol")
                     .style.background_gradient(cmap="RdYlGn", vmin=0.5, vmax=2.0),
                     use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("Gap Strategy Backtest")

    gap_threshold = st.slider("Gap threshold (%)", 1.0, 10.0, 2.0, 0.5)

    all_trades = []
    for symbol in WATCHLIST:
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna(subset=["Open","High","Low","Close"])
        df["prev_close"] = df["Close"].shift(1)
        df["gap_pct"]    = (df["Open"] - df["prev_close"]) / df["prev_close"] * 100
        df["ema50"]      = df["Close"].ewm(span=50, adjust=False).mean()
        ema12 = df["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df["Close"].ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        df["macd_bull"]  = (macd - macd.ewm(span=9, adjust=False).mean()) > 0
        df["vol_avg20"]  = df["Volume"].rolling(20).mean()

        for date, row in df[df["gap_pct"] >= gap_threshold].iterrows():
            if row["Volume"] < row["vol_avg20"] * 1.0: continue
            if row["prev_close"] < row["ema50"]:        continue
            if not row["macd_bull"]:                    continue

            entry = round(row["Open"] * 1.001, 2)
            sl    = round(row["prev_close"] * 1.002, 2)
            t2    = round(row["Open"] * (1 + row["gap_pct"] * 2 / 100), 2)
            t1    = round(row["Open"] * (1 + row["gap_pct"] / 100), 2)
            qty   = max(1, int(CAPITAL * 0.15 / entry))

            if row["Low"] <= sl:
                exit_p, reason = sl, "SL"
            elif row["High"] >= t2:
                exit_p, reason = t2, "T2"
            elif row["High"] >= t1:
                exit_p, reason = max(round(row["High"]*0.995,2), row["Close"]), "Trail"
            else:
                exit_p, reason = row["Close"], "Close"

            pnl_pct = (exit_p - entry) / entry * 100
            pnl_inr = (exit_p - entry) * qty - (entry + exit_p) * qty * BROKERAGE_FRAC
            all_trades.append({"symbol": symbol, "date": date, "gap_pct": round(row["gap_pct"],2),
                                "pnl_pct": round(pnl_pct,2), "pnl_inr": round(pnl_inr,2),
                                "exit_reason": reason, "win": pnl_inr > 0})

    if all_trades:
        bt = pd.DataFrame(all_trades)
        wins   = bt[bt["win"]]
        losses = bt[~bt["win"]]
        pf     = wins["pnl_inr"].sum() / abs(losses["pnl_inr"].sum()) if len(losses) else float("inf")

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Trades",   len(bt))
        col2.metric("Win Rate",        f"{bt['win'].mean()*100:.1f}%")
        col3.metric("Net P&L",         f"₹{bt['pnl_inr'].sum():+,.0f}")
        col4.metric("Profit Factor",   f"{pf:.2f}x")
        col5.metric("Avg Win / Loss",  f"₹{wins['pnl_inr'].mean():+.0f} / ₹{losses['pnl_inr'].mean():+.0f}" if len(wins) and len(losses) else "—")

        # Cumulative P&L
        bt_sorted = bt.sort_values("date")
        fig = go.Figure(go.Scatter(x=bt_sorted["date"], y=bt_sorted["pnl_inr"].cumsum(),
                                   fill="tozeroy", line_color="green"))
        fig.update_layout(title="Cumulative Backtest P&L", height=300,
                          yaxis_title="₹", margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig, use_container_width=True)

        # Per-symbol breakdown
        st.subheader("Per-Symbol Breakdown")
        sym_stats = bt.groupby("symbol").agg(
            Trades=("win","count"), WinRate=("win","mean"),
            NetPnL=("pnl_inr","sum"), AvgPnL=("pnl_inr","mean")
        ).reset_index()
        sym_stats["WinRate"] = (sym_stats["WinRate"]*100).round(1)
        sym_stats["NetPnL"]  = sym_stats["NetPnL"].round(2)
        sym_stats["AvgPnL"]  = sym_stats["AvgPnL"].round(2)
        st.dataframe(
            sym_stats.style
                .format({"WinRate": "{:.1f}%", "NetPnL": "₹{:+.2f}", "AvgPnL": "₹{:+.2f}"})
                .background_gradient(subset=["NetPnL"], cmap="RdYlGn"),
            use_container_width=True
        )

        # Exit reason pie
        reason_counts = bt["exit_reason"].value_counts()
        fig2 = go.Figure(go.Pie(labels=reason_counts.index, values=reason_counts.values, hole=0.4))
        fig2.update_layout(title="Exit Reasons", height=300, margin=dict(l=0,r=0,t=30,b=0))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No qualifying trades found for this threshold.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — NEWS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("News & Sentiment")

    selected = st.selectbox("Select stock", WATCHLIST)
    news_kb  = read_kb(selected, "news_history.json")
    articles = news_kb.get("news", [])

    if not articles:
        st.info(f"No news stored for {selected} yet. Run `python3 main.py` first.")
    else:
        st.caption(f"{len(articles)} articles stored | Last updated: {news_kb.get('updated_at','—')[:19]}")

        # Sentiment distribution chart
        sentiments = [a.get("sentiment", 0) for a in articles if "sentiment" in a]
        if sentiments:
            fig = go.Figure(go.Histogram(x=sentiments, nbinsx=20,
                                         marker_color=["green" if s > 0 else "red" for s in sentiments]))
            fig.update_layout(title="Sentiment Distribution", height=200,
                              xaxis_title="Sentiment (-1 to +1)",
                              margin=dict(l=0,r=0,t=30,b=0))
            st.plotly_chart(fig, use_container_width=True)

        # Articles table
        art_df = pd.DataFrame([{
            "Headline":  a["headline"][:90],
            "Source":    a.get("source","—"),
            "Sentiment": a.get("sentiment", 0),
            "Tier":      a.get("tier","—"),
            "Date":      str(a.get("fetched_at",""))[:10],
        } for a in reversed(articles)])

        st.dataframe(
            art_df.style
                .format({"Sentiment": "{:+.3f}"})
                .background_gradient(subset=["Sentiment"], cmap="RdYlGn", vmin=-1, vmax=1),
            use_container_width=True
        )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — INTRADAY ML BACKTEST
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.header("India Intraday ML Backtest (1h)")
    st.caption("GradientBoosting trained on 49 Nifty50 stocks × 3 years of 1h data. Out-of-sample: train 2yr, test 1yr.")

    from pathlib import Path as _Path
    DATA_DIR_1H = _Path("stocks_1h")
    MODEL_1H    = DATA_DIR_1H / "india_intraday_model.pkl"

    if not MODEL_1H.exists():
        st.warning("Intraday model not trained yet. Run: `python3 india_intraday_model.py train`")
    else:
        import pickle, warnings
        warnings.filterwarnings("ignore")

        col1, col2, col3 = st.columns(3)
        threshold  = col1.slider("Entry threshold", 0.50, 0.80, 0.55, 0.01)
        stop_pct   = col2.slider("Stop loss %", 0.5, 3.0, 1.0, 0.25)
        target_pct = col3.slider("Target %", 1.0, 5.0, 2.5, 0.25)
        trail_pct  = col1.slider("Trailing stop %", 0.25, 2.0, 0.5, 0.25)

        oos_mode = st.checkbox("Out-of-sample only (train 2yr, test 1yr)", value=True)
        selected_sym = st.selectbox("Stock (or All)", ["All"] + sorted(
            p.stem for p in DATA_DIR_1H.glob("*.parquet")
            if not any(x in p.stem for x in ["NIFTY","BANKNIFTY","VIX","model"])
        ))

        if st.button("▶ Run Backtest"):
            with st.spinner("Running..."):
                try:
                    from models.india_intraday_model import build_features, FORWARD_HOURS
                    from sklearn.ensemble import GradientBoostingClassifier
                    from sklearn.metrics import roc_auc_score

                    def _load_s(name):
                        p = DATA_DIR_1H / f"{name}.parquet"
                        if not p.exists(): return pd.Series(dtype=float)
                        d = pd.read_parquet(p)
                        d.index = pd.to_datetime(d.index, utc=True).tz_localize(None) if d.index.tz else d.index
                        return d["Close"]

                    nifty_s     = _load_s("NIFTY_1h")
                    banknifty_s = _load_s("BANKNIFTY_1h")
                    vix_s       = _load_s("VIX_1h")

                    parquets = sorted(p for p in DATA_DIR_1H.glob("*.parquet")
                                      if not any(x in p.stem for x in ["NIFTY","BANKNIFTY","VIX","model"]))
                    if selected_sym != "All":
                        parquets = [DATA_DIR_1H / f"{selected_sym}.parquet"]

                    TRAIN_END  = "2025-05-01"
                    TEST_START = "2025-05-01"
                    SLIPPAGE   = SLIPPAGE_FRAC
                    BROKERAGE  = BROKERAGE_FRAC
                    CAPITAL_BT = 10_000
                    POS_PCT    = 0.15

                    if oos_mode:
                        train_X, train_y, test_data = [], [], {}
                        for path in parquets:
                            df = pd.read_parquet(path)
                            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
                            df = df.dropna(subset=["Open","High","Low","Close","Volume"])
                            if len(df) < 200: continue
                            feat = build_features(df, nifty_s, banknifty_s, vix_s)
                            lbl  = ((df["Close"].shift(-FORWARD_HOURS)/df["Close"]-1)*100>1.0).astype(int)
                            comb = feat.join(lbl.rename("label")).dropna().iloc[:-FORWARD_HOURS]
                            tr = comb[comb.index < TRAIN_END]
                            te = comb[comb.index >= TEST_START]
                            if len(tr) > 50: train_X.append(tr.drop("label",axis=1)); train_y.append(tr["label"])
                            if len(te) > 10: test_data[path.stem] = (df[df.index >= TEST_START], te)

                        X_tr = pd.concat(train_X).reset_index(drop=True)
                        y_tr = pd.concat(train_y).reset_index(drop=True)
                        features = list(X_tr.columns)
                        bt_model = GradientBoostingClassifier(n_estimators=300, max_depth=4,
                                    learning_rate=0.05, subsample=0.8, max_features=0.8, random_state=42)
                        bt_model.fit(X_tr.fillna(0), y_tr)

                        all_test_X = pd.concat([v[1].drop("label",axis=1) for v in test_data.values()]).fillna(0)
                        all_test_y = pd.concat([v[1]["label"] for v in test_data.values()])
                        oos_auc = roc_auc_score(all_test_y, bt_model.predict_proba(all_test_X[features])[:,1])
                        st.metric("Out-of-sample AUC", f"{oos_auc:.4f}")
                    else:
                        with open(MODEL_1H, "rb") as f:
                            saved = pickle.load(f)
                        bt_model, features = saved["model"], saved["features"]
                        test_data = {}
                        for path in parquets:
                            df = pd.read_parquet(path)
                            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
                            df = df.dropna(subset=["Open","High","Low","Close","Volume"])
                            if len(df) < 200: continue
                            feat = build_features(df, nifty_s, banknifty_s, vix_s)
                            lbl  = ((df["Close"].shift(-FORWARD_HOURS)/df["Close"]-1)*100>1.0).astype(int)
                            comb = feat.join(lbl.rename("label")).dropna().iloc[:-FORWARD_HOURS]
                            test_data[path.stem] = (df, comb)

                    # Run trades
                    all_trades = []
                    for sym, (df_t, comb_t) in test_data.items():
                        proba = bt_model.predict_proba(comb_t.drop("label",axis=1)[features].fillna(0))[:,1]
                        close = df_t["Close"].reindex(comb_t.index).values
                        dates = comb_t.index
                        i = 0
                        while i < len(close) - 1:
                            if proba[i] < threshold: i += 1; continue
                            entry = close[i] * (1 + SLIPPAGE)
                            sl    = entry * (1 - stop_pct/100)
                            tgt   = entry * (1 + target_pct/100)
                            trail = sl
                            qty   = max(1, int(CAPITAL_BT * POS_PCT / entry))
                            ep, er = None, None; ed = dates[i]; j = i + 1
                            while j < len(close):
                                p = close[j]
                                if p > entry * (1 + trail_pct/100):
                                    nt = p * (1 - trail_pct/100)
                                    if nt > trail: trail = nt
                                if p <= trail:   ep, er = trail, "Trail/SL"; break
                                if p >= tgt:     ep, er = tgt,   "Target";   break
                                if dates[j].date() != ed.date() or j == len(close)-1:
                                    ep, er = p, "EOD"; break
                                j += 1
                            if ep is None: i += 1; continue
                            brok = (entry + ep) * qty * BROKERAGE
                            pnl  = (ep - entry) * qty - brok
                            all_trades.append({"symbol": sym, "datetime": ed, "hour": ed.hour,
                                               "proba": round(proba[i],4), "pnl_inr": round(pnl,2),
                                               "win": pnl > 0, "exit_reason": er})
                            i = j + 1

                    if not all_trades:
                        st.warning("No trades generated with these parameters.")
                    else:
                        trades_bt = pd.DataFrame(all_trades)
                        w = trades_bt[trades_bt["win"]]; l = trades_bt[~trades_bt["win"]]
                        pf = w["pnl_inr"].sum() / abs(l["pnl_inr"].sum()) if len(l) else float("inf")

                        c1,c2,c3,c4,c5 = st.columns(5)
                        c1.metric("Trades",       len(trades_bt))
                        c2.metric("Win Rate",      f"{trades_bt['win'].mean()*100:.1f}%")
                        c3.metric("Net P&L",       f"₹{trades_bt['pnl_inr'].sum():+,.0f}")
                        c4.metric("Profit Factor", f"{pf:.2f}x")
                        c5.metric("Avg Win/Loss",  f"₹{w['pnl_inr'].mean():+.0f} / ₹{l['pnl_inr'].mean():+.0f}" if len(w) and len(l) else "—")

                        # Cumulative P&L
                        cum = trades_bt.sort_values("datetime")["pnl_inr"].cumsum()
                        fig = go.Figure(go.Scatter(x=trades_bt.sort_values("datetime")["datetime"],
                                                   y=cum.values, fill="tozeroy",
                                                   line_color="green" if cum.iloc[-1]>0 else "red"))
                        fig.update_layout(title="Cumulative P&L", height=280,
                                          yaxis_title="₹", margin=dict(l=0,r=0,t=30,b=0))
                        st.plotly_chart(fig, use_container_width=True)

                        col_a, col_b = st.columns(2)

                        # Exit reason pie
                        rc = trades_bt["exit_reason"].value_counts()
                        fig2 = go.Figure(go.Pie(labels=rc.index, values=rc.values, hole=0.4))
                        fig2.update_layout(title="Exit Reasons", height=280, margin=dict(l=0,r=0,t=30,b=0))
                        col_a.plotly_chart(fig2, use_container_width=True)

                        # Win rate by hour
                        hr = trades_bt.groupby("hour").agg(wr=("win","mean"), cnt=("win","count")).reset_index()
                        fig3 = go.Figure(go.Bar(x=hr["hour"].astype(str)+":00",
                                                y=(hr["wr"]*100).round(1),
                                                text=(hr["wr"]*100).round(1),
                                                marker_color=["green" if v>50 else "red" for v in hr["wr"]]))
                        fig3.update_layout(title="Win Rate by Entry Hour", height=280,
                                           yaxis_title="%", margin=dict(l=0,r=0,t=30,b=0))
                        col_b.plotly_chart(fig3, use_container_width=True)

                        # Per-symbol table
                        if selected_sym == "All":
                            st.subheader("Per-Symbol")
                            sym_stats = trades_bt.groupby("symbol").agg(
                                Trades=("win","count"), WinRate=("win","mean"),
                                NetPnL=("pnl_inr","sum"), AvgPnL=("pnl_inr","mean")
                            ).reset_index()
                            sym_stats["WinRate"] = (sym_stats["WinRate"]*100).round(1)
                            st.dataframe(
                                sym_stats.style
                                    .format({"WinRate":"{:.1f}%","NetPnL":"₹{:+.2f}","AvgPnL":"₹{:+.2f}"})
                                    .background_gradient(subset=["NetPnL"], cmap="RdYlGn"),
                                use_container_width=True
                            )
                except Exception as e:
                    st.error(f"Error: {e}")
                    import traceback; st.code(traceback.format_exc())
