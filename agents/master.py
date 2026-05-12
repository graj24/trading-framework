"""
Master Agent — orchestrates all sub-agents, retrieves stock-specific RAG context,
and makes final trade decisions via LLM with rule-based fallback.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agents.base import Agent, AgentResult, AgentStatus
from agents.data_agent import DataAgent
from agents.news_agent import NewsAgent
from agents.technical_agent import TechnicalAgent
from agents.pattern_agent import PatternAgent
from agents.regime_agent import RegimeAgent
from agents.risk_manager import RiskManager
from core.knowledge_base import read_kb, kb_path

logger = logging.getLogger(__name__)


def _rag_context(symbol: str) -> dict:
    """Retrieve stock-specific context from knowledge base."""
    fundamentals = read_kb(symbol, "fundamentals.json")
    event_reactions = read_kb(symbol, "event_reactions.json")
    sector_corr = read_kb(symbol, "sector_correlation.json")
    signal_weights = read_kb(symbol, "signal_weights.json")
    patterns = read_kb(symbol, "patterns.json")

    # Top 2 correlations
    corrs = sector_corr.get("correlations", {})
    top_corr = sorted(corrs.items(), key=lambda x: abs(x[1]), reverse=True)[:2]

    return {
        "sector": fundamentals.get("sector", "Unknown"),
        "pe_ratio": fundamentals.get("pe_ratio"),
        "eps": fundamentals.get("eps"),
        "earnings_beat_avg": event_reactions.get("earnings_beat", {}).get("avg_reaction_pct"),
        "earnings_miss_avg": event_reactions.get("earnings_miss", {}).get("avg_reaction_pct"),
        "top_correlations": top_corr,
        "signal_weights": signal_weights,
        "pattern_summary": patterns.get("summary", {}),
    }


def _llm_decision(symbol: str, price: float, scores: dict, rag: dict, config: dict) -> dict:
    """Call LLM for trade decision. Falls back to rule-based on failure."""
    try:
        import litellm
        llm_cfg = config.get("llm", {})

        prompt = f"""You are an expert Indian stock trader. Make a trading decision based on the data below.
Return ONLY valid JSON with no markdown: {{"decision": "BUY|SELL|HOLD", "confidence": 0-100, "entry": {price}, "stop_loss": 0.0, "target": 0.0, "reasoning": "one sentence"}}

STOCK: {symbol}
CURRENT PRICE: {price}
TECHNICAL SCORE: {scores.get('technical_score', 0)}/10 (RSI: {scores.get('rsi', 0):.1f}, MACD: {scores.get('macd_signal', 'N/A')})
NEWS SENTIMENT: {scores.get('sentiment', 0):.2f} (Tier: {scores.get('tier', 'None')})
PATTERN EV: {scores.get('pattern_ev', 0):.2f}% (Win rate: {scores.get('win_rate', 0):.0f}%)
MARKET REGIME: {scores.get('regime', 'unknown')}
SECTOR: {rag.get('sector', 'Unknown')}
PE RATIO: {rag.get('pe_ratio', 'N/A')}
EARNINGS BEAT AVG REACTION: {rag.get('earnings_beat_avg', 'N/A')}%
SIGNAL WEIGHTS: {json.dumps(rag.get('signal_weights', {}), default=str)[:200]}"""

        response = litellm.completion(
            model="openai/deepseek",
            api_base=llm_cfg.get("litellm_base_url", "http://localhost:4000"),
            messages=[{"role": "user", "content": prompt}],
            temperature=llm_cfg.get("temperature", 0.1),
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"LLM unavailable ({type(e).__name__}), using rule-based fallback")
        return _rule_based_decision(price, scores)


def _rule_based_decision(price: float, scores: dict) -> dict:
    """Fallback rule-based decision when LLM is unavailable."""
    tech = scores.get("technical_score", 0)
    sentiment = scores.get("sentiment", 0)
    pattern_ev = scores.get("pattern_ev", 0)
    regime = scores.get("regime", "unknown")
    tier = scores.get("tier")

    if tier == 1:
        return {"decision": "SKIP", "confidence": 95, "entry": price,
                "stop_loss": 0.0, "target": 0.0, "reasoning": "TIER 1 news emergency"}

    if tech >= 7 and sentiment >= 0 and pattern_ev > 0 and regime != "trending_bear":
        sl = round(price * 0.99, 2)
        target = round(price * 1.025, 2)
        confidence = min(95, int(tech * 8 + sentiment * 10 + pattern_ev * 5))
        return {"decision": "BUY", "confidence": confidence, "entry": price,
                "stop_loss": sl, "target": target, "reasoning": "Strong technical + positive sentiment + positive pattern EV"}

    if tech <= 3 or sentiment <= -0.5:
        return {"decision": "SKIP", "confidence": 70, "entry": price,
                "stop_loss": 0.0, "target": 0.0, "reasoning": "Weak technicals or negative sentiment"}

    return {"decision": "HOLD", "confidence": 50, "entry": price,
            "stop_loss": 0.0, "target": 0.0, "reasoning": "Mixed signals, no clear edge"}


class MasterAgent(Agent):
    """Orchestrates all sub-agents and synthesizes a final trade decision."""

    def __init__(self, config: dict):
        super().__init__("MasterAgent", config)
        self.data_agent = DataAgent(config)
        self.news_agent = NewsAgent(config)
        self.technical_agent = TechnicalAgent(config)
        self.pattern_agent = PatternAgent(config)
        self.regime_agent = RegimeAgent(config)
        self.risk_manager = RiskManager(config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        symbol = (context or {}).get("symbol")
        if not symbol:
            return self._error("No symbol in context")
        return self.run_for_stock(symbol)

    def run_for_stock(self, symbol: str) -> AgentResult:
        self._status = AgentStatus.RUNNING
        logger.info(f"MasterAgent analyzing {symbol}")

        # 1. Run all sub-agents
        tech_result = self.technical_agent.run({"symbol": symbol})
        news_result = self.news_agent.run({"symbol": symbol})
        pattern_result = self.pattern_agent.run({"symbol": symbol})
        regime_result = self.regime_agent.run({"symbol": symbol})

        tech = tech_result.data if tech_result.ok() else {}
        news = news_result.data if news_result.ok() else {}
        pattern = pattern_result.data if pattern_result.ok() else {}
        regime = regime_result.data if regime_result.ok() else {}

        # 2. Get current price
        price = tech.get("current_price", 0.0)
        if not price:
            try:
                import yfinance as yf
                t = yf.Ticker(symbol + ".NS")
                hist = t.history(period="1d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
            except Exception:
                price = 0.0

        # 3. Aggregate scores
        scores = {
            "technical_score": tech.get("technical_score", 0),
            "rsi": tech.get("rsi", 50),
            "macd_signal": tech.get("macd_signal", "neutral"),
            "sentiment": news.get("sentiment", 0),
            "tier": news.get("tier"),
            "pattern_ev": pattern.get("expected_value", 0),
            "win_rate": pattern.get("win_rate", 50),
            "regime": regime.get("regime", "unknown"),
        }

        # 4. Emergency override: TIER 1 news
        if scores["tier"] == 1:
            logger.warning(f"{symbol}: TIER 1 news detected — emergency skip")
            return self._result({
                "symbol": symbol, "decision": "SKIP", "confidence": 95,
                "entry_price": price, "stop_loss": 0.0, "target": 0.0,
                "position_size": 0.0, "reasoning": "TIER 1 emergency news",
                "agent_scores": scores,
            })

        # 5. RAG context retrieval
        rag = _rag_context(symbol)

        # 6. LLM decision (with fallback)
        llm_out = _llm_decision(symbol, price, scores, rag, self.config)

        decision = llm_out.get("decision", "HOLD")
        confidence = llm_out.get("confidence", 50)
        reasoning = llm_out.get("reasoning", "")

        # 7. Confidence threshold
        if confidence < 60 and decision == "BUY":
            decision = "HOLD"
            reasoning = f"Confidence too low ({confidence}%) — holding"

        # 8. Risk manager for position sizing
        position_size = 0.0
        stop_loss = llm_out.get("stop_loss", 0.0)
        target = llm_out.get("target", 0.0)

        if decision == "BUY":
            risk_result = self.risk_manager.run({
                "symbol": symbol,
                "entry_price": price,
                "win_rate": scores["win_rate"],
                "avg_win": pattern.get("avg_win", 2.0),
                "avg_loss": abs(pattern.get("avg_loss", -1.5)),
                "open_positions": [],
                "daily_pnl_pct": 0.0,
            })
            if risk_result.ok():
                position_size = risk_result.data.get("position_size", 0.0)
                if not stop_loss:
                    stop_loss = risk_result.data.get("stop_loss", price * 0.99)
                if not risk_result.data.get("allowed", True):
                    decision = "SKIP"
                    reasoning = risk_result.data.get("reason", "Risk limit reached")

        result = {
            "symbol": symbol,
            "decision": decision,
            "confidence": confidence,
            "entry_price": price,
            "stop_loss": stop_loss,
            "target": target,
            "position_size": position_size,
            "reasoning": reasoning,
            "agent_scores": scores,
        }

        logger.info(f"{symbol}: {decision} (confidence={confidence}%) — {reasoning}")
        return self._result(result)


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    master = MasterAgent(config)
    result = master.run_for_stock("RELIANCE")

    if result.ok():
        d = result.data
        print(f"\n{'='*55}")
        print(f"  MASTER AGENT DECISION: {d['symbol']}")
        print(f"{'='*55}")
        print(f"  Decision    : {d['decision']}")
        print(f"  Confidence  : {d['confidence']}%")
        print(f"  Entry Price : ₹{d['entry_price']}")
        print(f"  Stop Loss   : ₹{d['stop_loss']}")
        print(f"  Target      : ₹{d['target']}")
        print(f"  Position    : ₹{d['position_size']:.2f}")
        print(f"  Reasoning   : {d['reasoning']}")
        print(f"\n  Agent Scores:")
        for k, v in d["agent_scores"].items():
            print(f"    {k}: {v}")
        print(f"{'='*55}")
