# Project Context — Autonomous Trading Framework

## What this is
An autonomous multi-PM trading framework for the Indian equity market (NSE). Multiple LLM-based Portfolio Manager agents compete to generate the highest returns. Currently paper trading; live trading via Zerodha is wired but not yet enabled.

## Infrastructure

| | |
|---|---|
| Trading EC2 | 13.206.3.62 (m7i-flex.large, 8GB) — trading daemon + FastAPI + PM agents |
| Multica EC2 | 13.232.42.85 (t3.small) — Multica agent management platform |
| Trading UI | http://13.206.3.62 |
| Multica board | http://13.232.42.85:3000 |
| SSH key | ~/.ssh/trading-key.pem |
| Repo | https://github.com/graj24/trading-framework |

Auto-deploy: every push to `main` triggers GitHub Actions → EC2 git pull → restart services.

## Codebase layout
```
agents/          trading agents (technical, news, pattern, regime, risk, execution, learning, discovery, intraday, earnings, sector rotation)
core/            scheduler, broker (Paper + Zerodha), Groww client, knowledge base, backtester
models/          ml_model.py (daily), india_intraday_model.py (1h), stocks_1h/
api/             FastAPI REST + WebSocket backend
frontend/        React + TypeScript UI (Bloomberg Terminal)
scripts/         dashboard.py (Streamlit), backtest scripts, simulate_day.py
ripple/          FinBERT sentiment subsystem
pm_prompts/      Portfolio Manager agent prompts (TEMPLATE.md, PM1, PM2)
setup/           AWS deployment scripts, systemd services, README (full infra docs)
paper_trades.db  SQLite trade ledger (shared by all PMs)
stocks/<SYM>/    per-stock knowledge base (price history, fundamentals, news, patterns)
config.yaml      runtime config (watchlist, risk, schedule)
.env             secrets (never committed)
Makefile         all common tasks (make deploy, make ssh, make logs, make update-key, etc.)
```

## Key commands
```bash
make deploy        # push code to EC2
make ssh           # SSH into trading EC2
make logs          # tail trading daemon logs
make status        # check all services
make update-key KEY=X VALUE=Y   # update a secret on EC2 + locally
make test          # run pytest (34 tests)
```

## Services on trading EC2
- `trading-daemon` — `python main.py --schedule` (24/7 IST scheduler)
- `trading-api` — `uvicorn api.main:app` (port 8000)
- `nginx` — reverse proxy (port 80 → 8000)
- `multica daemon` — connects EC2 to Multica for PM agent task execution

## Portfolio Managers
PM agents are LLMs competing to make the most money. Managed via Multica board.
- PM1: multi-signal pipeline (technical + FinBERT + DTW + regime + 2 ML models + LLM)
- PM2: competes against PM1, full freedom to use any strategy
- Full prompts: `pm_prompts/PM1_full_prompt.md`, `pm_prompts/PM2_full_prompt.md`
- Template for new PMs: `pm_prompts/TEMPLATE.md`
- Scoreboard: `sqlite3 paper_trades.db "SELECT pm_id, SUM(pnl_inr) FROM trades WHERE outcome!='open' GROUP BY pm_id"`

## Decision pipeline (per stock)
TechnicalAgent + NewsAgent + PatternAgent + RegimeAgent → ML models (daily + 1h) → LLM (Groq Llama-3.3-70B) → confidence gate (≥60%) + hard filters (trend up, MACD bullish, vol ≥1×) → RiskManager (half-Kelly + ATR SL) → ExecutionAgent (SQLite)

## Important docs
- `setup/README.md` — full infrastructure, ops, cost, AWS resource IDs
- `setup/MULTICA.md` — Multica agent platform (adding PMs, daemon ops, server ops)
- `docs/user-guide.md` — install, configure, run
- `docs/technical-reference.md` — all modules, APIs, schemas
- `docs/analysis/` — architecture, data flow, agents, decision pipeline, issues, roadmap
- `pm_prompts/` — PM agent prompts
