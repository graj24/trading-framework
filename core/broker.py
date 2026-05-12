"""
Broker abstraction layer — PaperBroker and ZerodhaBroker.
"""
from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

BROKERAGE_PCT = 0.0003   # 0.03% per side
BROKERAGE_MAX = 20.0     # ₹20 max per order
STT_SELL_PCT = 0.001     # 0.1% STT on sell


class Broker(ABC):
    @abstractmethod
    def place_order(self, symbol: str, qty: int, order_type: str,
                    price: float, sl: float = 0.0, tag: str = "") -> str:
        """Returns order_id."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict: ...

    @abstractmethod
    def get_ltp(self, symbol: str) -> float: ...

    def brokerage(self, trade_value: float) -> float:
        return min(trade_value * BROKERAGE_PCT, BROKERAGE_MAX)

    def stt(self, trade_value: float) -> float:
        return trade_value * STT_SELL_PCT


class PaperBroker(Broker):
    """Simulates order execution using yfinance prices."""

    CIRCUIT_BREAKER_ORDERS = 5   # max orders per minute
    CIRCUIT_BREAKER_WINDOW = 60  # seconds

    def __init__(self):
        self._orders: dict[str, dict] = {}
        self._positions: dict[str, dict] = {}
        self._order_times: deque = deque()

    def _check_circuit_breaker(self):
        now = time.time()
        # Remove orders older than window
        while self._order_times and now - self._order_times[0] > self.CIRCUIT_BREAKER_WINDOW:
            self._order_times.popleft()
        if len(self._order_times) >= self.CIRCUIT_BREAKER_ORDERS:
            raise RuntimeError(
                f"Circuit breaker triggered: {self.CIRCUIT_BREAKER_ORDERS} orders in {self.CIRCUIT_BREAKER_WINDOW}s"
            )
        self._order_times.append(now)

    def place_order(self, symbol: str, qty: int, order_type: str = "MARKET",
                    price: float = 0.0, sl: float = 0.0, tag: str = "") -> str:
        self._check_circuit_breaker()
        ltp = self.get_ltp(symbol)
        fill_price = price if (order_type == "LIMIT" and price > 0) else ltp
        order_id = str(uuid.uuid4())[:8]
        trade_value = fill_price * qty
        brok = self.brokerage(trade_value)

        self._orders[order_id] = {
            "order_id": order_id, "symbol": symbol, "qty": qty,
            "order_type": order_type, "price": fill_price, "sl": sl,
            "status": "COMPLETE", "tag": tag,
            "brokerage": brok, "placed_at": datetime.now().isoformat(),
        }

        # Update positions
        if symbol in self._positions:
            self._positions[symbol]["qty"] += qty
        else:
            self._positions[symbol] = {"symbol": symbol, "qty": qty, "avg_price": fill_price}

        logger.info(f"PaperBroker: {order_type} {qty}×{symbol} @ ₹{fill_price:.2f} | brok=₹{brok:.2f} | id={order_id}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELLED"
            logger.info(f"PaperBroker: Order {order_id} cancelled")
            return True
        return False

    def get_positions(self) -> list[dict]:
        return list(self._positions.values())

    def get_order_status(self, order_id: str) -> dict:
        return self._orders.get(order_id, {"status": "NOT_FOUND"})

    def get_ltp(self, symbol: str) -> float:
        try:
            t = yf.Ticker(symbol + ".NS")
            hist = t.history(period="1d")
            return float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        except Exception:
            return 0.0


class ZerodhaBroker(Broker):
    """Zerodha Kite API integration. Requires kiteconnect package."""

    def __init__(self, api_key: str, access_token: str):
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            raise ImportError(
                "kiteconnect not installed. Run: pip install kiteconnect\n"
                "Then generate access_token at: https://kite.trade/connect/login"
            )
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        logger.info("ZerodhaBroker initialized")

    def place_order(self, symbol: str, qty: int, order_type: str = "MARKET",
                    price: float = 0.0, sl: float = 0.0, tag: str = "") -> str:
        from kiteconnect import KiteConnect
        params = {
            "tradingsymbol": symbol,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "quantity": qty,
            "order_type": order_type,
            "product": "MIS",  # intraday
            "tag": tag,
        }
        if order_type == "LIMIT":
            params["price"] = price
        if sl:
            params["trigger_price"] = sl
        order_id = self.kite.place_order(variety="regular", **params)
        logger.info(f"Zerodha order placed: {order_id}")
        return str(order_id)

    def cancel_order(self, order_id: str) -> bool:
        self.kite.cancel_order(variety="regular", order_id=order_id)
        return True

    def get_positions(self) -> list[dict]:
        return self.kite.positions().get("net", [])

    def get_order_status(self, order_id: str) -> dict:
        orders = self.kite.orders()
        for o in orders:
            if str(o["order_id"]) == str(order_id):
                return o
        return {"status": "NOT_FOUND"}

    def get_ltp(self, symbol: str) -> float:
        data = self.kite.ltp(f"NSE:{symbol}")
        return float(data[f"NSE:{symbol}"]["last_price"])


def get_broker(config: dict) -> Broker:
    """Factory: returns PaperBroker or ZerodhaBroker based on config."""
    mode = config["trading"]["mode"]
    if mode == "paper":
        return PaperBroker()
    elif mode == "live":
        import os
        return ZerodhaBroker(
            api_key=os.getenv("ZERODHA_API_KEY", ""),
            access_token=os.getenv("ZERODHA_ACCESS_TOKEN", ""),
        )
    raise ValueError(f"Unknown trading mode: {mode}")


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    broker = get_broker(config)
    print(f"\nBroker: {type(broker).__name__}")

    ltp = broker.get_ltp("RELIANCE")
    print(f"RELIANCE LTP: ₹{ltp:.2f}")

    order_id = broker.place_order("RELIANCE", qty=1, order_type="MARKET", tag="test")
    print(f"Order placed: {order_id}")
    print(f"Status: {broker.get_order_status(order_id)}")

    broker.cancel_order(order_id)
    print(f"After cancel: {broker.get_order_status(order_id)['status']}")
    print(f"Positions: {broker.get_positions()}")
