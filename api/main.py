"""FastAPI application — Bloomberg Terminal backend."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so `core`, `agents` etc. are importable
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routers import trades, signals, market, config, backtest, agents, ws, candles

app = FastAPI(
    title="Bloomberg Terminal API",
    description="Trading framework REST + WebSocket API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(trades.router)
app.include_router(signals.router)
app.include_router(market.router)
app.include_router(config.router)
app.include_router(backtest.router)
app.include_router(agents.router)
app.include_router(ws.router)
app.include_router(candles.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve built frontend if it exists
FRONTEND_DIST = ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
