"""
Full stock analysis demo — shows everything happening step by step.
Usage: python test_stock.py RELIANCE
"""
from __future__ import annotations

import sys
import yaml
from dotenv import load_dotenv
from core.logger import setup_logging

load_dotenv()
with open("config.yaml") as f:
    config = yaml.safe_load(f)
setup_logging(config)

import logging
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

import json
from datetime import datetime
import pandas as pd

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "RELIANCE"

SEP  = "=" * 60
SEP2 = "-" * 60

def header(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def section(title):
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)

# ─────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  AUTONOMOUS TRADING FRAMEWORK — FULL ANALYSIS")
print(f"  Stock: {SYMBOL}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*60}")

# ─────────────────────────────────────────────────────────────
header("STEP 1: DATA AGENT — Knowledge Base")
from agents.data_agent import DataAgent
from core.knowledge_base import read_kb, kb_path

data_agent = DataAgent(config)
print(f"  Building/updating knowledge base for {SYMBOL}...")
data_agent.build_kb(SYMBOL)

# Show what's in the KB
path = kb_path(SYMBOL)
fundamentals = read_kb(SYMBOL, "fundamentals.json")
earnings     = read_kb(SYMBOL, "earnings_history.json")
corp         = read_kb(SYMBOL, "corporate_actions.json")
sector_corr  = read_kb(SYMBOL, "sector_correlation.json")
event_react  = read_kb(SYMBOL, "event_reactions.json")
sig_weights  = read_kb(SYMBOL, "signal_weights.json")

print(f"\n  📁 Knowledge Base: stocks/{SYMBOL}/")
print(f"  Company     : {fundamentals.get('company_name', 'N/A')}")
print(f"  Sector      : {fundamentals.get('sector', 'N/A')}")
print(f"  Industry    : {fundamentals.get('industry', 'N/A')}")
print(f"  Market Cap  : ₹{fundamentals.get('market_cap', 0):,.0f}" if fundamentals.get('market_cap') else "  Market Cap  : N/A")
print(f"  PE Ratio    : {fundamentals.get('pe_ratio', 'N/A')}")
print(f"  EPS         : {fundamentals.get('eps', 'N/A')}")
print(f"  52W High    : ₹{fundamentals.get('52w_high', 'N/A')}")
print(f"  52W Low     : ₹{fundamentals.get('52w_low', 'N/A')}")
print(f"  Debt/Equity : {fundamentals.get('debt_to_equity', 'N/A')}")
print(f"  ROE         : {fundamentals.get('roe', 'N/A')}")

# Price history stats
price_df = data_agent.load_price_history(SYMBOL)
if price_df is not None:
    print(f"\n  📈 Price History: {len(price_df)} trading days")
    print(f"  From        : {price_df.index.min().date()}")
    print(f"  To          : {price_df.index.max().date()}")
    current_price = float(price_df["Close"].iloc[-1])
    prev_close    = float(price_df["Close"].iloc[-2])
    day_change    = (current_price - prev_close) / prev_close * 100
    week_change   = (current_price - float(price_df["Close"].iloc[-6])) / float(price_df["Close"].iloc[-6]) * 100
    month_change  = (current_price - float(price_df["Close"].iloc[-22])) / float(price_df["Close"].iloc[-22]) * 100
    print(f"  Current     : ₹{current_price:.2f}")
    print(f"  Day change  : {day_change:+.2f}%")
    print(f"  Week change : {week_change:+.2f}%")
    print(f"  Month change: {month_change:+.2f}%")

# Earnings history
quarters = earnings.get("quarters", [])
if quarters:
    print(f"\n  📊 Earnings History ({len(quarters)} quarters):")
    for q in quarters[:4]:
        rxn = q.get("price_reaction_pct")
        rxn_str = f"{rxn:+.1f}%" if rxn is not None else "N/A"
        print(f"    {q['date']}: price reaction = {rxn_str}")

# Sector correlations
corrs = sector_corr.get("correlations", {})
if corrs:
    print(f"\n  🔗 Sector Correlations:")
    for k, v in sorted(corrs.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
        bar = "█" * int(abs(v) * 10)
        print(f"    {k:12s}: {v:+.3f}  {bar}")

# Signal weights
print(f"\n  ⚖️  Current Signal Weights (learned):")
for k, v in sig_weights.items():
    if k != "updated_at":
        print(f"    {k:20s}: {v:.4f}")

# ─────────────────────────────────────────────────────────────
header("STEP 1.5: EARNINGS CALENDAR — Overnight Catalyst Check")
from agents.earnings_calendar_agent import EarningsCalendarAgent, compute_historical_earnings_reaction, score_result

earnings_agent = EarningsCalendarAgent(config)

# Historical earnings reaction
hist_reaction = compute_historical_earnings_reaction(SYMBOL)
print(f"  Historical Earnings Reactions for {SYMBOL}:")
if hist_reaction.get("total_count", 0) > 0:
    print(f"  Avg day-of reaction  : {hist_reaction['avg_day_of_pct']:+.2f}%")
    print(f"  Avg 3-day reaction   : {hist_reaction['avg_three_day_pct']:+.2f}%")
    print(f"  Best reaction        : {hist_reaction['best_reaction']:+.2f}%")
    print(f"  Worst reaction       : {hist_reaction['worst_reaction']:+.2f}%")
    print(f"  Positive rate        : {hist_reaction['positive_count']}/{hist_reaction['total_count']} ({hist_reaction['positive_count']/hist_reaction['total_count']:.0%})")
    print(f"\n  Individual reactions:")
    for r in hist_reaction.get("reactions", [])[-5:]:
        emoji = "📈" if r["day_of_pct"] > 0 else "📉"
        print(f"    {emoji} {r['date']}: day-of {r['day_of_pct']:+.1f}% | 3-day {r['three_day_pct']:+.1f}%")
else:
    print(f"  No historical reaction data yet (need more earnings dates in KB)")

# Check for overnight filings
print(f"\n  Checking overnight NSE/BSE filings...")
overnight = earnings_agent.overnight_monitor()
overnight_signals = [s for s in overnight.get("signals", []) if s["symbol"] == SYMBOL]
if overnight_signals:
    for s in overnight_signals:
        emoji = "🟢" if "BUY" in s["action"] else "🔴"
        print(f"  {emoji} FILING DETECTED: {s.get('filing_subject', '')[:60]}")
        print(f"     Verdict   : {s['verdict']}")
        print(f"     Signal    : {s['action']} (confidence={s['confidence']}%)")
        print(f"     Reasoning : {s['reasoning']}")
else:
    print(f"  No result filings detected for {SYMBOL} in last 24 hours")

# Next earnings date
next_date = earnings_agent.evening_prep()
for entry in next_date.get("upcoming", []):
    if entry["symbol"] == SYMBOL and entry.get("next_earnings_date"):
        print(f"\n  Next earnings date   : {entry['next_earnings_date']}")
        if entry.get("watch"):
            print(f"  ⚠️  EARNINGS IN {entry.get('days_away', '?')} DAY(S) — WATCH CLOSELY")

# ─────────────────────────────────────────────────────────────
header("STEP 1.6: PRE-OPEN MONITOR — Gap Analysis")
from agents.pre_open_monitor import PreOpenMonitor, fetch_preopen_prices, analyze_gap, GAP_UP_THRESHOLD

print(f"  Fetching pre-open price for {SYMBOL}...")
preopen_monitor = PreOpenMonitor(config)
all_preopen = fetch_preopen_prices()

# Try NSE first, fallback to yfinance
from agents.pre_open_monitor import _fetch_preopen_yfinance
if SYMBOL not in all_preopen:
    fallback = _fetch_preopen_yfinance([SYMBOL])
    all_preopen.update(fallback)

if SYMBOL in all_preopen:
    gap_data = all_preopen[SYMBOL]
    gap_pct = gap_data.get("gap_pct", 0)
    gap_emoji = "🟢" if gap_pct > 1.5 else ("🔴" if gap_pct < -1.5 else "⚪")

    print(f"\n  {gap_emoji} Pre-Open Data:")
    print(f"  Previous Close  : ₹{gap_data['prev_close']:.2f}")
    print(f"  Pre-Open Price  : ₹{gap_data['preopen_price']:.2f}")
    print(f"  Gap             : {gap_pct:+.2f}%")
    print(f"  Pre-Open Volume : {gap_data.get('preopen_volume', 0):,}")

    if abs(gap_pct) >= GAP_UP_THRESHOLD:
        print(f"\n  Significant gap detected — running deep analysis...")
        gap_analysis = analyze_gap(SYMBOL, gap_data)
        print(f"\n  Gap Analysis:")
        print(f"  Trade Signal    : {gap_analysis['trade_signal']}")
        print(f"  Confidence      : {gap_analysis['confidence']}%")
        if gap_analysis["entry"]:
            print(f"  Entry           : ₹{gap_analysis['entry']:.2f}")
        if gap_analysis["stop_loss"]:
            print(f"  Stop Loss       : ₹{gap_analysis['stop_loss']:.2f}")
        if gap_analysis["target"]:
            print(f"  Target          : ₹{gap_analysis['target']:.2f}")
        print(f"  Reasoning       : {gap_analysis['reasoning']}")
        if gap_analysis["catalysts"]:
            print(f"\n  Catalysts:")
            for c in gap_analysis["catalysts"]:
                print(f"    ✅ {c}")
        if gap_analysis["risks"]:
            print(f"\n  Risks:")
            for r in gap_analysis["risks"]:
                print(f"    ⚠️  {r}")
    else:
        print(f"\n  Gap {gap_pct:+.2f}% is below threshold (±{GAP_UP_THRESHOLD}%) — no gap trade signal")
else:
    print(f"  Could not fetch pre-open data for {SYMBOL}")


from agents.news_agent import NewsAgent

news_agent = NewsAgent(config)
news_result = news_agent.analyze(SYMBOL)

print(f"  Sentiment Score : {news_result['sentiment']:+.3f}  (-1=very negative, +1=very positive)")
print(f"  Event Tier      : {news_result['tier']} (1=emergency, 2=re-evaluate, 3=monitor, None=no news)")
print(f"  News Items Found: {news_result['news_count']}")

if news_result["headlines"]:
    print(f"\n  Top Headlines:")
    for h in news_result["headlines"]:
        print(f"    • {h}")
else:
    print(f"\n  No news fetched (scrapers may be blocked — normal for free tier)")

# Show stored news count
stored = read_kb(SYMBOL, "news_history.json")
print(f"  Stored in KB    : {len(stored.get('news', []))} historical news items")

# ─────────────────────────────────────────────────────────────
header("STEP 3: TECHNICAL ANALYSIS AGENT")
from agents.technical_agent import TechnicalAgent

tech_agent = TechnicalAgent(config)
tech_result = tech_agent.run({"symbol": SYMBOL})
tech = tech_result.data

print(f"  Composite Score : {tech.get('technical_score', 0)}/10")
print(f"  Trend           : {tech.get('trend', 'N/A').upper()}")
print(f"  RSI(14)         : {tech.get('rsi', 0):.2f}  (>70=overbought, <30=oversold)")
print(f"  MACD Signal     : {tech.get('macd_signal', 'N/A').upper()}")
print(f"  ADX(14)         : {tech.get('adx', 0):.2f}  (>25=strong trend)")
print(f"  ATR(14)         : ₹{tech.get('atr', 0):.2f}  (daily volatility range)")
print(f"  BOS Detected    : {tech.get('bos_detected', False)}  (Break of Structure)")

ema20  = tech.get("ema20", 0)
ema50  = tech.get("ema50", 0)
ema200 = tech.get("ema200", 0)
price  = tech.get("current_price", current_price)
print(f"\n  Moving Averages:")
print(f"    EMA20  : ₹{ema20:.2f}  {'✅ Price above' if price > ema20 else '❌ Price below'}")
print(f"    EMA50  : ₹{ema50:.2f}  {'✅ Price above' if price > ema50 else '❌ Price below'}")
print(f"    EMA200 : ₹{ema200:.2f}  {'✅ Price above' if price > ema200 else '❌ Price below'}")

supports    = tech.get("support_levels", [])
resistances = tech.get("resistance_levels", [])
if supports:
    nearest_support = max([s for s in supports if s < price], default=None)
    print(f"\n  Nearest Support    : ₹{nearest_support:.2f}" if nearest_support else "\n  Nearest Support    : N/A")
if resistances:
    nearest_resistance = min([r for r in resistances if r > price], default=None)
    print(f"  Nearest Resistance : ₹{nearest_resistance:.2f}" if nearest_resistance else "  Nearest Resistance : N/A")

# Score breakdown
print(f"\n  Score Breakdown (each criterion = 1 point):")
criteria = [
    ("Price > EMA20",        price > ema20),
    ("Price > EMA50",        price > ema50),
    ("Price > EMA200",       price > ema200),
    ("RSI 40-60 (healthy)",  40 <= tech.get("rsi", 0) <= 60),
    ("MACD bullish",         tech.get("macd_signal") == "bullish"),
    ("ADX > 25 (trending)",  tech.get("adx", 0) > 25),
    ("BOS detected",         tech.get("bos_detected", False)),
]
for name, passed in criteria:
    print(f"    {'✅' if passed else '❌'} {name}")

# ─────────────────────────────────────────────────────────────
header("STEP 4: PATTERN RECOGNITION AGENT")
from agents.pattern_agent import PatternAgent

pattern_agent = PatternAgent(config)
pattern_result = pattern_agent.run({"symbol": SYMBOL})
pattern = pattern_result.data

print(f"  Expected Value  : {pattern.get('expected_value', 0):+.2f}%  (positive = edge exists)")
print(f"  Win Rate        : {pattern.get('win_rate', 0):.1f}%")
print(f"  Similar Patterns: {pattern.get('similar_count', 0)} found in history")

top_match = pattern.get("pattern_match")
if top_match:
    print(f"\n  Best Historical Match:")
    print(f"    Date       : {top_match.get('date', 'N/A')}")
    print(f"    Similarity : {top_match.get('similarity', 0):.4f}")
    print(f"    Outcome    : {top_match.get('outcome_10d_pct', 0):+.2f}% over next 10 days")

# Load all patterns
patterns_kb = read_kb(SYMBOL, "patterns.json")
all_patterns = patterns_kb.get("patterns", [])
if len(all_patterns) > 1:
    print(f"\n  All {len(all_patterns)} Similar Historical Setups:")
    for p in all_patterns:
        outcome = p.get("outcome_10d_pct", 0)
        emoji = "📈" if outcome > 0 else "📉"
        print(f"    {emoji} {p['date']}  similarity={p['similarity']:.3f}  outcome={outcome:+.2f}%")

# ─────────────────────────────────────────────────────────────
header("STEP 5: MARKET REGIME DETECTION")
from agents.regime_agent import RegimeAgent

regime_agent = RegimeAgent(config)
regime_result = regime_agent.run({"symbol": SYMBOL})
regime = regime_result.data

regime_name = regime.get("regime", "unknown")
regime_emojis = {
    "trending_bull": "🐂",
    "trending_bear": "🐻",
    "ranging": "↔️",
    "high_volatility": "⚡",
}
print(f"  Regime          : {regime_emojis.get(regime_name, '❓')} {regime_name.upper()}")
print(f"  Confidence      : {regime.get('confidence', 0):.1%}")
print(f"  ADX(14)         : {regime.get('adx', 0):.2f}  (>25 = trending)")
print(f"  20d Volatility  : {regime.get('volatility', 0):.1f}% annualized")
print(f"  India VIX       : {regime.get('vix', 'N/A')}")

adj = regime.get("strategy_adjustments", {})
if adj:
    print(f"\n  Strategy Adjustments for this regime:")
    print(f"    Position size multiplier : {adj.get('position_size_multiplier', 1.0)}×")
    print(f"    Prefer                   : {adj.get('prefer', 'N/A')}")
    print(f"    Avoid                    : {adj.get('avoid', 'N/A')}")

# ─────────────────────────────────────────────────────────────
header("STEP 6: RISK MANAGER — Position Sizing")
from agents.risk_manager import RiskManager

risk_manager = RiskManager(config)
risk_result = risk_manager.run({
    "symbol": SYMBOL,
    "entry_price": price,
    "win_rate": pattern.get("win_rate", 50),
    "avg_win": patterns_kb.get("summary", {}).get("avg_win", 2.0),
    "avg_loss": abs(patterns_kb.get("summary", {}).get("avg_loss", -1.5)),
    "open_positions": [],
    "daily_pnl_pct": 0.0,
})
risk = risk_result.data

capital = config["trading"]["capital"]
print(f"  Capital         : ₹{capital:,}")
print(f"  Trade Allowed   : {'✅ Yes' if risk.get('allowed') else '❌ No — ' + risk.get('reason', '')}")
print(f"  Position Size   : ₹{risk.get('position_size', 0):.2f}  ({risk.get('position_size', 0)/capital*100:.1f}% of capital)")
print(f"  Stop Loss       : ₹{risk.get('stop_loss', 0):.2f}  ({abs(price - risk.get('stop_loss', price))/price*100:.2f}% below entry)")
print(f"\n  Risk Rules:")
print(f"    Max loss/trade : {config['risk']['max_loss_per_trade_pct']}% = ₹{capital * config['risk']['max_loss_per_trade_pct'] / 100:.0f}")
print(f"    Max loss/day   : {config['risk']['max_loss_per_day_pct']}% = ₹{capital * config['risk']['max_loss_per_day_pct'] / 100:.0f}")
print(f"    Max positions  : {config['risk']['max_open_positions']}")
print(f"    Trailing stop  : activates after {config['risk']['trailing_stop_trigger_pct']}% profit")

# ─────────────────────────────────────────────────────────────
header("STEP 7: MASTER AGENT — LLM Final Decision")
from agents.master import MasterAgent

print(f"  Sending all data to DeepSeek LLM via litellm proxy...")
print(f"  RAG context loaded from knowledge base...")

master = MasterAgent(config)
master_result = master.run_for_stock(SYMBOL)
decision_data = master_result.data

decision   = decision_data.get("decision", "N/A")
confidence = decision_data.get("confidence", 0)
entry      = decision_data.get("entry_price", 0)
sl         = decision_data.get("stop_loss", 0)
target     = decision_data.get("target", 0)
pos_size   = decision_data.get("position_size", 0)
reasoning  = decision_data.get("reasoning", "")

decision_emojis = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡", "SKIP": "⚫"}
print(f"\n  {decision_emojis.get(decision, '❓')} DECISION: {decision}")
print(f"  Confidence  : {confidence}%")
print(f"  Entry Price : ₹{entry:.2f}")
if sl:
    print(f"  Stop Loss   : ₹{sl:.2f}  ({abs(entry-sl)/entry*100:.2f}% risk)")
if target:
    rr = abs(target - entry) / abs(entry - sl) if sl and entry != sl else 0
    print(f"  Target      : ₹{target:.2f}  ({abs(target-entry)/entry*100:.2f}% gain, R:R = 1:{rr:.1f})")
if pos_size:
    print(f"  Position    : ₹{pos_size:.2f}  ({pos_size/capital*100:.1f}% of capital)")
print(f"\n  LLM Reasoning:")
print(f"  \"{reasoning}\"")

# ─────────────────────────────────────────────────────────────
header("STEP 8: EXECUTION — Paper Trade")
from agents.execution_agent import ExecutionAgent

executor = ExecutionAgent(config)

if decision == "BUY" and confidence >= 60:
    print(f"  ✅ Signal qualifies — executing paper trade...")
    trade = executor.execute_trade(
        symbol=SYMBOL,
        entry_price=entry,
        stop_loss=sl or entry * 0.99,
        target=target or entry * 1.025,
        position_size=pos_size or 1000.0,
        reasoning=reasoning,
    )
    print(f"  Trade ID    : {trade['trade_id']}")
    print(f"  Entry       : ₹{trade['entry_price']:.2f} (with 0.05% slippage)")
    print(f"  Stop Loss   : ₹{trade['stop_loss']:.2f}")
    print(f"  Target      : ₹{trade['target']:.2f}")
    print(f"  Position    : ₹{trade['position_size']:.2f}")
    print(f"\n  Trade logged to paper_trades.db ✅")
else:
    print(f"  ⏭️  No trade executed (decision={decision}, confidence={confidence}%)")

# ─────────────────────────────────────────────────────────────
header("STEP 9: BACKTESTING — Historical Strategy Performance")
from core.backtester import Backtester, MACDStrategy, RSIStrategy

bt = Backtester()
print(f"  Running MACD strategy backtest on {SYMBOL} (2021-2025)...")
result = bt.run(SYMBOL, MACDStrategy(), start_date="2021-01-01", walk_forward_splits=3)
print(f"  Trades      : {result.total_trades}")
print(f"  Win Rate    : {result.win_rate:.1f}%")
print(f"  Avg Gain    : {result.avg_gain_pct:+.2f}%")
print(f"  Avg Loss    : {result.avg_loss_pct:+.2f}%")
print(f"  Expected Val: {result.expected_value:+.2f}%")
print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
print(f"  Max Drawdown: {result.max_drawdown_pct:.2f}%")
print(f"  Total Return: {result.total_return_pct:+.2f}%")

# ─────────────────────────────────────────────────────────────
header("STEP 10: DAILY REPORT")
report = executor.daily_report()
print(f"  Date        : {report['date']}")
print(f"  Trades today: {report['trades']}")
print(f"  Total P&L   : ₹{report['total_pnl_inr']:+.2f} ({report['total_pnl_pct']:+.2f}%)")
print(f"  Win Rate    : {report['win_rate']:.0f}%")

# ─────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  ANALYSIS COMPLETE — {SYMBOL}")
print(f"  Decision: {decision_emojis.get(decision, '❓')} {decision} @ ₹{entry:.2f}")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(SEP)
