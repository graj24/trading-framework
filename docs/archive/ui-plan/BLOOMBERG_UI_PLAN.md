# Bloomberg-Level UI — Detailed Implementation Plan

**Branch**: `feat/bloomberg-ui`  
**Status**: Plan only — ready for implementation pickup  
**Estimated effort**: 5–7 days for a senior frontend engineer  
**Stack**: FastAPI (Python) + Vite + React 18 + TypeScript

---

## 1. Vision

A professional trading terminal UI indistinguishable from a Bloomberg/Refinitiv terminal:

- **Dark, dense, information-rich** — every pixel earns its place
- **Real-time** — WebSocket-driven live P&L, position updates, agent status
- **Interactive pipeline** — animated data flow you can click into
- **Zero page reloads** — SPA with client-side routing
- **Keyboard-first** — power users navigate without a mouse

Reference aesthetics: Bloomberg Terminal, Refinitiv Eikon, TradingView, Robinhood Pro.

---

## 2. Tech Stack

### Backend
| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | `^0.111` | REST + WebSocket API |
| `uvicorn[standard]` | `^0.29` | ASGI server with hot reload |
| `python-dotenv` | already in deps | env loading |
| `websockets` | `^12` | real-time push |

### Frontend
| Package | Version | Purpose |
|---------|---------|---------|
| `vite` | `^5` | build tool + dev server |
| `react` | `^18` | UI framework |
| `typescript` | `^5` | type safety |
| `@tremor/react` | `^3` | Bloomberg-style dashboard components |
| `reactflow` | `^11` | interactive pipeline diagram |
| `recharts` | `^2` | P&L curves, candlesticks |
| `lightweight-charts` | `^4` | TradingView-quality candlestick charts |
| `@tanstack/react-query` | `^5` | data fetching + caching |
| `zustand` | `^4` | global state (positions, regime, alerts) |
| `react-router-dom` | `^6` | client-side routing |
| `framer-motion` | `^11` | animations (agent status pulses, transitions) |
| `tailwindcss` | `^3` | utility CSS |
| `clsx` | `^2` | conditional classnames |

---

## 3. Repository Structure

```
trading-framework/
├── api/                            ← NEW: FastAPI backend
│   ├── main.py                     # app factory, CORS, lifespan
│   ├── routers/
│   │   ├── trades.py               # GET /trades, GET /trades/{id}
│   │   ├── signals.py              # GET /signals/{symbol}, POST /signals/run
│   │   ├── agents.py               # GET /agents/status
│   │   ├── config.py               # GET/PATCH /config, GET/PATCH /env
│   │   ├── backtest.py             # POST /backtest/gap, POST /backtest/intraday
│   │   ├── market.py               # GET /market/regime, GET /market/sectors
│   │   └── ws.py                   # WS /ws/live — real-time feed
│   ├── schemas/                    # Pydantic response models
│   │   ├── trade.py
│   │   ├── signal.py
│   │   └── agent.py
│   └── deps.py                     # shared dependencies (DB conn, config)
│
├── frontend/                       ← NEW: Vite + React app
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── src/
│       ├── main.tsx                # entry point
│       ├── App.tsx                 # router + layout shell
│       ├── store/
│       │   ├── useMarketStore.ts   # regime, VIX, sector returns (zustand)
│       │   ├── useTradeStore.ts    # open positions, P&L
│       │   └── useAlertStore.ts    # anomaly alerts queue
│       ├── hooks/
│       │   ├── useWebSocket.ts     # WS connection + reconnect
│       │   ├── useTrades.ts        # react-query wrapper
│       │   └── useSignals.ts
│       ├── pages/
│       │   ├── Terminal.tsx        # main trading terminal (default view)
│       │   ├── Pipeline.tsx        # interactive agent flow diagram
│       │   ├── Backtest.tsx        # backtest runner + results
│       │   ├── Setup.tsx           # API key wizard
│       │   └── Replay.tsx          # date-range replay harness
│       ├── components/
│       │   ├── layout/
│       │   │   ├── TopBar.tsx      # ticker tape + regime badge + clock
│       │   │   ├── Sidebar.tsx     # nav + quick stats
│       │   │   └── AlertBanner.tsx # anomaly alert strip
│       │   ├── terminal/
│       │   │   ├── PositionsTable.tsx
│       │   │   ├── PnLChart.tsx    # lightweight-charts candlestick
│       │   │   ├── OrderBook.tsx   # open orders + history
│       │   │   ├── SignalPanel.tsx # per-symbol score breakdown
│       │   │   └── NewsStream.tsx  # live news + sentiment badges
│       │   ├── pipeline/
│       │   │   ├── PipelineFlow.tsx     # React Flow canvas
│       │   │   ├── AgentNode.tsx        # custom node: icon + score + status
│       │   │   ├── DecisionNode.tsx     # MasterAgent / LLM / Risk nodes
│       │   │   └── AnimatedEdge.tsx     # pulsing data-flow edges
│       │   ├── charts/
│       │   │   ├── SectorHeatmap.tsx
│       │   │   ├── CumulativePnL.tsx
│       │   │   └── SignalRadar.tsx  # radar chart of 7 agent scores
│       │   └── setup/
│       │       ├── KeyForm.tsx
│       │       └── ConnectionTest.tsx
│       └── lib/
│           ├── api.ts              # typed fetch wrappers
│           ├── ws.ts               # WebSocket client
│           └── formatters.ts       # ₹ formatting, % formatting
│
├── ui/                             ← existing Streamlit (keep as fallback)
└── ...
```

---

## 4. Page-by-Page Specification

### 4.1 Terminal (main view) — `/`

The default landing page. Modelled on a Bloomberg multi-panel layout.

**Layout**: 3-column grid

```
┌─────────────────────────────────────────────────────────────────┐
│  TOP BAR: [NIFTY 24,532 ▲0.4%] [VIX 14.2] [REGIME: BULL] [IST 09:32:14] │
├──────────────┬──────────────────────────────┬───────────────────┤
│  WATCHLIST   │   PRICE CHART (TradingView)  │   SIGNAL PANEL    │
│  (left 20%)  │   (center 50%)               │   (right 30%)     │
│              │                              │                   │
│  RELIANCE ▲  │  [candlestick + volume]      │  Tech:  ████ 7/10 │
│  TCS      ▼  │  [EMA20/50/200 overlays]     │  News:  ███  +0.3 │
│  HDFCBANK ▲  │  [entry/SL/target lines]     │  Pat:   ██   +1.2%│
│  ...         │                              │  ML:    ████ 0.72 │
│              │                              │  Regime: BULL     │
│              │                              │  [BUY / HOLD / SKIP]│
├──────────────┴──────────────────────────────┴───────────────────┤
│  POSITIONS TABLE (full width, collapsible)                       │
│  Symbol | Entry | LTP | P&L% | P&L₹ | SL | Target | Age        │
├─────────────────────────────────────────────────────────────────┤
│  NEWS STREAM (scrolling, left 60%) │ TRADE LOG (right 40%)      │
└─────────────────────────────────────────────────────────────────┘
```

**Key interactions**:
- Click any symbol in watchlist → chart + signal panel update instantly
- Hover a position row → highlight entry/SL/target on chart
- Real-time P&L cells flash green/red on update (framer-motion)
- Top bar ticker tape scrolls continuously (CSS animation)
- Keyboard shortcut `G` → go-to symbol search modal

**Real-time data** (WebSocket events):
```typescript
type LiveEvent =
  | { type: "ltp_update";    symbol: string; price: number; change_pct: number }
  | { type: "trade_opened";  trade: Trade }
  | { type: "trade_closed";  trade: Trade }
  | { type: "pnl_update";    total_pnl_inr: number; total_pnl_pct: number }
  | { type: "alert";         message: string; severity: "info"|"warn"|"error" }
  | { type: "regime_change"; regime: string; confidence: number }
```

---

### 4.2 Pipeline — `/pipeline`

The "wow" page. An interactive animated diagram of the full decision pipeline.

**Layout**: Full-screen React Flow canvas with a detail panel on the right.

```
┌─────────────────────────────────────────────────────┬──────────┐
│                                                     │  DETAIL  │
│   [Data]──►[TechAgent]──►                           │  PANEL   │
│   [News]──►[NewsAgent]──►                           │          │
│   [KB]──►[PatternAgent]──►[MasterAgent]──►[LLM]──►  │  Click   │
│   [NSE]──►[RegimeAgent]──►               [Rules]──► │  any     │
│   [pkl]──►[MLDaily]──────►[Filters]──►[Risk]──►[Exec]│  node   │
│   [pkl]──►[MLIntraday]───►                           │  for     │
│   [NSE]──►[EarningsAgent]►                           │  details │
│                                                     │          │
│   [Exec]──►[SQLite]──►[LearningAgent]──►[MasterAgent]│          │
└─────────────────────────────────────────────────────┴──────────┘
```

**Node types**:

| Node | Visual | Content |
|------|--------|---------|
| Data source | Blue rectangle | Name, last-updated timestamp |
| Agent | Card with icon | Name, last score, status dot (pulsing green = active) |
| MasterAgent | Larger gold card | Composite score, last decision |
| LLM | Purple card | Model name, last latency, last response |
| Filter | Diamond shape | Filter name, last pass/fail |
| Risk | Orange card | Kelly fraction, position size |
| ExecutionAgent | Green card | Mode (paper/live/shadow), last trade |
| SQLite | Cylinder | Trade count, last write |
| LearningAgent | Teal card | Weights updated, last symbol |

**Animated edges**:
- Edges pulse with a travelling dot animation when data is flowing
- Edge colour = signal strength (green = strong, grey = idle, red = blocked)
- Hover an edge → tooltip shows what data flows through it

**Click a node** → right panel shows:
- Last run timestamp
- Input/output data (formatted JSON)
- Score history sparkline (last 20 runs)
- Link to relevant source file

**Controls**:
- Zoom / pan (React Flow built-in)
- "Run for symbol" button → triggers a live analysis cycle and animates the flow in real-time
- Minimap in bottom-right corner

---

### 4.3 Backtest — `/backtest`

A proper backtest workbench.

**Layout**: Split — controls left, results right

**Controls panel**:
- Strategy selector: Gap | Intraday ML | Replay
- Date range picker
- Symbol multi-select (or "All NIFTY 50")
- Strategy parameters (threshold, SL%, target%, trail%)
- "Run" button → streams results back via SSE

**Results panel** (updates as backtest streams):
- Headline metrics: trades, win rate, net P&L, profit factor, max drawdown, Sharpe
- Equity curve (cumulative P&L over time) — lightweight-charts
- Drawdown chart (below equity curve, red fill)
- Monthly returns heatmap (calendar grid, green/red cells)
- Per-symbol breakdown table (sortable)
- Exit reason donut chart
- Trade distribution histogram (P&L per trade)

**Bloomberg-style detail**: clicking any trade in the table jumps to that date on the equity curve and highlights the trade.

---

### 4.4 Setup — `/setup`

Multi-step wizard. Not a form dump — a guided flow.

**Steps**:
1. **LLM** — Groq key input + live test (shows model response)
2. **Market data** — Groww key + test (shows RELIANCE LTP)
3. **Alerts** — Telegram bot + send test message
4. **Broker** — choose paper / shadow / live → conditional broker fields
5. **Review** — summary of all configured services with status badges
6. **Save** — writes to `.env`, shows confirmation

Each step has a progress indicator at the top. Can skip optional steps.

---

### 4.5 Replay — `/replay`

Date-range simulation harness.

- Date range picker (start / end)
- Symbol selector
- Gap threshold slider
- "Run Replay" → streams day-by-day results
- Shows a "time travel" progress bar as it processes each day
- Final report identical to Backtest page

---

## 5. FastAPI Backend Specification

### Endpoints

```
GET  /api/trades                    → list of trades (filterable by date, outcome)
GET  /api/trades/{id}               → single trade detail
GET  /api/signals/{symbol}          → last signal scores for symbol
POST /api/signals/run               → trigger a fresh analysis cycle (async)
GET  /api/agents/status             → last-run status of all agents
GET  /api/market/regime             → current NIFTY regime
GET  /api/market/sectors            → 30d sector returns
GET  /api/config                    → current config.yaml (sanitised)
PATCH /api/config                   → update config.yaml fields
GET  /api/env/status                → which keys are set (masked values)
PATCH /api/env                      → write keys to .env
POST /api/env/test/{service}        → test a specific API key
POST /api/backtest/gap              → run gap backtest (streams NDJSON)
POST /api/backtest/intraday         → run intraday ML backtest (streams NDJSON)
POST /api/replay                    → run date-range replay (streams NDJSON)
WS   /ws/live                       → real-time event stream
```

### WebSocket protocol

The `/ws/live` endpoint pushes events every second during market hours, on-demand otherwise:

```python
# Server pushes JSON lines:
{"type": "ltp_update",   "symbol": "RELIANCE", "price": 2847.50, "change_pct": 0.42}
{"type": "pnl_update",   "total_pnl_inr": 1240.50, "total_pnl_pct": 1.24}
{"type": "trade_opened", "trade": {...}}
{"type": "alert",        "message": "P&L approaching daily limit", "severity": "warn"}
```

### Streaming backtest responses

Backtest endpoints stream NDJSON so the UI can show progress:

```python
# Each line is a JSON object:
{"type": "progress", "pct": 12, "symbol": "RELIANCE", "date": "2024-03-15"}
{"type": "trade",    "symbol": "TCS", "pnl_inr": 340.0, "exit_reason": "T2"}
{"type": "summary",  "trades": 142, "win_rate": 61.2, "net_pnl": 18420.0}
```

---

## 6. Design System

### Colour palette (Bloomberg-inspired)

```css
--bg-primary:    #0a0e17;   /* near-black background */
--bg-secondary:  #111827;   /* card backgrounds */
--bg-tertiary:   #1f2937;   /* hover states, borders */
--text-primary:  #f9fafb;   /* main text */
--text-secondary:#9ca3af;   /* labels, captions */
--text-muted:    #4b5563;   /* disabled, placeholders */

--green:         #00d4aa;   /* profit, bullish, active */
--red:           #ff4d4d;   /* loss, bearish, error */
--gold:          #f59e0b;   /* warnings, highlights */
--blue:          #3b82f6;   /* links, info, selected */
--purple:        #8b5cf6;   /* LLM nodes */
--orange:        #f97316;   /* risk nodes */

--border:        #1f2937;
--border-active: #3b82f6;
```

### Typography

```css
font-family: "JetBrains Mono", "Fira Code", monospace;  /* numbers, prices */
font-family: "Inter", system-ui, sans-serif;             /* labels, prose */
```

Numbers always in monospace. Prices right-aligned. Positive values in `--green`, negative in `--red`.

### Component conventions

- **Metric card**: dark background, label in muted text above, value in large bold, delta badge below
- **Table rows**: 32px height, alternating `bg-secondary`/`bg-primary`, hover highlight
- **Status dot**: 8px circle, pulsing CSS animation when active
- **Flash on update**: cell background flashes green/red for 300ms on value change (framer-motion)
- **Tooltips**: dark, 12px, appear on hover with 100ms delay

---

## 7. Implementation Order

A developer picking this up should work in this order:

### Phase 1 — Backend (2 days)
1. `api/main.py` — FastAPI app with CORS, lifespan, uvicorn config
2. `api/deps.py` — DB connection, config loader
3. `api/schemas/` — Pydantic models for Trade, Signal, Agent
4. `api/routers/trades.py` — read from `paper_trades.db`
5. `api/routers/signals.py` — read from knowledge base JSON
6. `api/routers/market.py` — regime + sector returns
7. `api/routers/config.py` + `api/routers/env.py` — read/write config
8. `api/routers/ws.py` — WebSocket live feed (start with mock data, wire real data later)
9. `api/routers/backtest.py` — streaming backtest via `core/backtester.py`

**Verify**: `uvicorn api.main:app --reload` + hit `/docs` (FastAPI auto-generates Swagger UI)

### Phase 2 — Frontend scaffold (0.5 days)
1. `npm create vite@latest frontend -- --template react-ts`
2. Install all dependencies (see §2)
3. Configure Tailwind, path aliases, API base URL via env var
4. `App.tsx` — router + layout shell (TopBar + Sidebar + `<Outlet>`)
5. `lib/api.ts` — typed fetch wrappers for all endpoints
6. `lib/ws.ts` — WebSocket client with auto-reconnect

### Phase 3 — Terminal page (1.5 days)
1. `components/layout/TopBar.tsx` — ticker tape, regime badge, clock
2. `components/terminal/PositionsTable.tsx` — with flash-on-update
3. `components/charts/CumulativePnL.tsx` — lightweight-charts
4. `components/terminal/SignalPanel.tsx` — score bars
5. `components/terminal/NewsStream.tsx` — scrolling news feed
6. `pages/Terminal.tsx` — assemble the 3-column layout
7. Wire WebSocket → zustand store → component re-renders

### Phase 4 — Pipeline page (1.5 days)
1. `components/pipeline/AgentNode.tsx` — custom React Flow node
2. `components/pipeline/DecisionNode.tsx`
3. `components/pipeline/AnimatedEdge.tsx` — travelling dot animation
4. `pages/Pipeline.tsx` — React Flow canvas + detail panel
5. Wire "Run for symbol" → POST `/api/signals/run` → animate edges

### Phase 5 — Backtest + Setup + Replay (1 day)
1. `pages/Backtest.tsx` — streaming results via SSE/fetch
2. `pages/Setup.tsx` — multi-step wizard
3. `pages/Replay.tsx` — date-range replay

### Phase 6 — Polish (0.5 days)
1. Keyboard shortcuts (`G` for symbol search, `R` for refresh, `B` for backtest)
2. Loading skeletons (not spinners)
3. Error boundaries
4. Responsive breakpoints (1280px minimum target)
5. `README` for the frontend (how to run, env vars)

---

## 8. Running the full stack

```bash
# Terminal 1 — API
cd trading-framework
source .venv/bin/activate
uvicorn api.main:app --reload --port 8000

# Terminal 2 — Frontend
cd trading-framework/frontend
npm install
npm run dev        # → http://localhost:5173

# Or build for production:
npm run build      # → frontend/dist/
# FastAPI can serve the dist/ folder as static files
```

Environment variable for the frontend:
```bash
# frontend/.env.local
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws/live
```

---

## 9. What the Streamlit UI keeps doing

The existing `ui/` Streamlit app is **not deleted**. It stays as:
- A lightweight fallback when Node.js isn't available
- A quick internal tool for the setup wizard (before the React version is built)

Run it with: `streamlit run ui/app.py`

---

## 10. Open questions for the implementer

1. **Authentication**: Should the API require a token? (Recommended if exposed beyond localhost)
2. **Live LTP in WebSocket**: Use Groww polling (every 1s) or yfinance (every 60s)? Groww is preferred but requires a valid access token.
3. **Candlestick data**: `lightweight-charts` needs OHLCV at 1m/5m resolution for intraday. Currently only daily + 1h parquet files exist. Add a `/api/candles/{symbol}?interval=5m` endpoint backed by yfinance.
4. **"Run for symbol" latency**: A full `MasterAgent.run_for_stock()` cycle takes 5–15s (LLM call). Show a progress stream or just a spinner?
5. **Mobile**: Bloomberg Terminal is desktop-only. Recommend targeting 1280px+ only and not investing in mobile layout.
