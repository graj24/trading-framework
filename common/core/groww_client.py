"""
Groww Live Data Client — replaces NSE polling and yfinance for live prices.

Authentication flow:
  1. Generate checksum = SHA256(secret + timestamp)
  2. POST /v1/token/api/access with api_key + checksum → get access_token
  3. Use access_token as Bearer in all subsequent requests
  Access token expires daily at 6:00 AM — auto-refreshed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.groww.in/v1"
HEADERS = {
    "Accept": "application/json",
    "X-API-VERSION": "1.0",
}


def generate_checksum(secret: str, timestamp: str) -> str:
    """SHA256(secret + timestamp) as hex string."""
    return hashlib.sha256((secret + timestamp).encode()).hexdigest()


def get_access_token(api_key: str, secret: str) -> Optional[str]:
    """Exchange API key + secret for a session access token."""
    timestamp = str(int(time.time()))
    checksum = generate_checksum(secret, timestamp)
    try:
        resp = requests.post(
            f"{BASE_URL}/token/api/access",
            headers={**HEADERS, "Authorization": f"Bearer {api_key}"},
            json={"key_type": "approval", "checksum": checksum, "timestamp": timestamp},
            timeout=10,
        )
        data = resp.json()
        # Groww returns token at top level, not inside payload
        token = data.get("token") or data.get("payload", {}).get("token")
        if token:
            logger.info("Groww access token obtained successfully")
            return token
        logger.warning(f"Groww token error: {data}")
        return None
    except Exception as e:
        logger.error(f"Groww token fetch failed: {e}")
        return None


class GrowwClient:
    def __init__(self, api_key: str = "", secret: str = ""):
        self.api_key = api_key or os.getenv("GROWW_API_KEY", "")
        self.secret  = secret  or os.getenv("GROWW_SECRET", "")
        # Use pre-generated access token if available (refreshed daily)
        self._access_token: Optional[str] = os.getenv("GROWW_ACCESS_TOKEN", "")
        self._token_fetched_at: float = time.time() if self._access_token else 0
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if self._access_token:
            self.session.headers["Authorization"] = f"Bearer {self._access_token}"

    def _ensure_token(self) -> bool:
        """Auto-refresh token if missing or older than 6 hours."""
        age = time.time() - self._token_fetched_at
        if self._access_token and age < 6 * 3600:
            return True
        token = get_access_token(self.api_key, self.secret)
        if token:
            self._access_token = token
            self._token_fetched_at = time.time()
            self.session.headers["Authorization"] = f"Bearer {token}"
            return True
        # Fallback: try using api_key directly as bearer
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        return False

    def _get(self, path: str, params: dict) -> Optional[dict]:
        self._ensure_token()
        try:
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=8)
            data = resp.json()
            if data.get("status") == "SUCCESS":
                return data.get("payload", {})
            logger.warning(f"Groww API error: {data}")
            return None
        except Exception as e:
            logger.debug(f"Groww request failed: {e}")
            return None

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        """
        Batch LTP for up to 50 symbols.
        symbols: list of NSE symbols e.g. ['RELIANCE', 'INFY']
        Returns: {'RELIANCE': 1388.5, 'INFY': 1176.0}
        """
        exchange_symbols = ",".join(f"NSE_{s}" for s in symbols)
        payload = self._get("/live-data/ltp", {
            "segment": "CASH",
            "exchange_symbols": exchange_symbols,
        })
        if not payload:
            return {}
        # Response: {"NSE_RELIANCE": 1388.5, ...}
        return {k.replace("NSE_", ""): float(v) for k, v in payload.items()}

    def get_quote(self, symbol: str) -> Optional[dict]:
        """
        Full quote for a single symbol: LTP, OHLC, volume, VWAP, depth.
        """
        payload = self._get("/live-data/quote", {
            "exchange": "NSE",
            "segment": "CASH",
            "trading_symbol": symbol,
        })
        if not payload:
            return None

        ohlc = payload.get("ohlc", {})
        return {
            "symbol": symbol,
            "ltp": payload.get("last_price", 0),
            "open": ohlc.get("open", 0),
            "high": ohlc.get("high", 0),
            "low": ohlc.get("low", 0),
            "close": ohlc.get("close", 0),
            "volume": payload.get("volume", 0),
            "vwap": payload.get("average_price", 0),
            "day_change_pct": payload.get("day_change_perc", 0),
            "upper_circuit": payload.get("upper_circuit_limit", 0),
            "lower_circuit": payload.get("lower_circuit_limit", 0),
            "week_52_high": payload.get("week_52_high", 0),
            "week_52_low": payload.get("week_52_low", 0),
            "bid": payload.get("bid_price", 0),
            "ask": payload.get("offer_price", 0),
        }

    def get_ohlc_batch(self, symbols: list[str]) -> dict[str, dict]:
        """
        Batch OHLC for up to 50 symbols.
        Returns: {'RELIANCE': {'open': 1420, 'high': 1450, 'low': 1380, 'close': 1388}}
        """
        exchange_symbols = ",".join(f"NSE_{s}" for s in symbols)
        payload = self._get("/live-data/ohlc", {
            "segment": "CASH",
            "exchange_symbols": exchange_symbols,
        })
        if not payload:
            return {}
        result = {}
        for key, ohlc in payload.items():
            sym = key.replace("NSE_", "")
            if isinstance(ohlc, dict):
                result[sym] = ohlc
        return result


# Singleton — reuse across the app
_client: Optional[GrowwClient] = None

def get_groww_client() -> GrowwClient:
    global _client
    if _client is None:
        _client = GrowwClient()
    return _client


if __name__ == "__main__":
    import yaml
    from common.core.logger import setup_logging

    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    client = GrowwClient()
    watchlist = config.get("watchlist", [])[:5]

    print(f"\n{'='*55}")
    print(f"  GROWW LIVE DATA TEST")
    print(f"{'='*55}")

    # Test batch LTP
    print(f"\n📊 Batch LTP ({len(watchlist)} stocks):")
    ltps = client.get_ltp(watchlist)
    if ltps:
        for sym, price in ltps.items():
            print(f"  {sym:<14}: ₹{price:.2f}")
    else:
        print("  Failed — check API key or market hours")

    # Test full quote
    print(f"\n📋 Full Quote: RELIANCE")
    quote = client.get_quote("RELIANCE")
    if quote:
        for k, v in quote.items():
            if v:
                print(f"  {k:<20}: {v}")
    else:
        print("  Failed — check API key or market hours")

    # Test batch OHLC
    print(f"\n📈 Batch OHLC:")
    ohlcs = client.get_ohlc_batch(watchlist)
    if ohlcs:
        for sym, ohlc in ohlcs.items():
            print(f"  {sym:<14}: O={ohlc.get('open',0):.0f} H={ohlc.get('high',0):.0f} L={ohlc.get('low',0):.0f} C={ohlc.get('close',0):.0f}")
    else:
        print("  Failed — check API key or market hours")

    print(f"\n{'='*55}")
