"""Telegram alert system for trade notifications."""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.debug("Telegram alerts disabled (no token/chat_id)")

    def send(self, message: str) -> bool:
        if not self.enabled:
            logger.info(f"[ALERT] {message}")
            return False
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")
            return False

    def trade_alert(self, symbol: str, decision: str, entry: float,
                    sl: float, target: float, confidence: int) -> bool:
        emoji = "🟢" if decision == "BUY" else "🔴"
        msg = (f"{emoji} <b>{decision} {symbol}</b>\n"
               f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | Target: ₹{target:.2f}\n"
               f"Confidence: {confidence}%")
        return self.send(msg)

    def exit_alert(self, symbol: str, outcome: str, pnl_pct: float, pnl_inr: float) -> bool:
        emoji = "✅" if outcome == "win" else ("🚨" if outcome == "emergency_exit" else "🔴")
        msg = (f"{emoji} <b>EXIT {symbol}</b> ({outcome})\n"
               f"P&L: {pnl_pct:+.2f}% (₹{pnl_inr:+.2f})")
        return self.send(msg)

    def daily_summary(self, report: dict) -> bool:
        pnl = report.get("total_pnl_inr", 0)
        emoji = "📈" if pnl >= 0 else "📉"
        msg = (f"{emoji} <b>Daily Summary — {report.get('date', '')}</b>\n"
               f"Trades: {report.get('trades', 0)} | Win rate: {report.get('win_rate', 0):.0f}%\n"
               f"P&L: ₹{pnl:+.2f} ({report.get('total_pnl_pct', 0):+.2f}%)")
        return self.send(msg)

    def emergency_alert(self, symbol: str, tier: int, headline: str) -> bool:
        msg = (f"🚨 <b>EMERGENCY: {symbol} (TIER {tier})</b>\n"
               f"{headline}")
        return self.send(msg)
