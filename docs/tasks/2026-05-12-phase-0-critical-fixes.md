# Phase 0 — Critical Fixes (Task Breakdown)

**Roadmap reference**: `docs/specs/2026-05-12-phased-roadmap-design.md`
**Duration**: ~2-3 weeks
**Status**: Not started

Each task has an owner (initially all unassigned), acceptance criteria, and dependencies.

## T0.1 — Groww API as Primary Data Source
- **Why**: yfinance is delayed, flaky, and rate-limited for NSE. Groww gives real-time quotes from the actual exchange.
- **Scope**:
  - Audit `core/groww_client.py` — confirm `get_ltp`, `get_ohlc_batch`, `get_quote` work.
  - Add WebSocket support if Groww offers it (check API docs).
  - Route `agents/data_agent.py` price history through Groww historical endpoint.
  - Route `agents/technical_agent.py` intraday (5m) through Groww if available, else keep yfinance fallback with clear logging.
  - Environment variables for API keys (don't hardcode).
- **Acceptance**: `DataAgent.build_kb()` pulls from Groww for at least one symbol end-to-end. yfinance only fires as explicit fallback.
- **Dependencies**: Groww API credentials in `.env`.

## T0.2 — Wire ATR-Based Stop-Loss and Target
- **Why**: Fixed 1%/2.5% SL/target is barely outside daily noise. ATR-based levels respect volatility.
- **Scope**:
  - In `agents/master.py`, replace `stop_loss = price * 0.99` with `risk_manager.atr_stop_loss(price, symbol, atr_multiplier=1.5)`.
  - Add `atr_target()` function: `entry + N × ATR` where N is tunable (start at 2.5× for ~1.67 R:R).
  - Also pass ATR and support/resistance levels into LLM prompt so it can pick structure-based levels when LLM is the decision-maker.
- **Acceptance**: A new trade shows SL/target levels that scale with stock volatility (RELIANCE SL at ~1.5-2%, a high-vol stock at ~3-4%).
- **Dependencies**: None. `risk_manager.atr_stop_loss()` is already implemented.

## T0.3 — Realistic Transaction Cost Model
- **Why**: Current model (0.05% slippage + 0.03% brokerage) underestimates real cost. Missing STT, exchange charges, stamp duty, GST.
- **Scope**:
  - Create `core/transaction_costs.py` with functions for intraday vs delivery cost computation.
  - Indian charges to model:
    - STT: 0.025% sell (intraday), 0.1% buy+sell (delivery)
    - NSE transaction charges: 0.00325%
    - Stamp duty: 0.003% buy side
    - SEBI charges: ₹10/crore
    - GST: 18% on (brokerage + exchange charges + SEBI)
    - Groww brokerage: ₹20 flat per order or 0.1% (whichever lower) — verify with Groww
  - Update `agents/execution_agent.py` `_pnl()` to use this module.
- **Acceptance**: A ₹10K round-trip delivery trade shows ~0.15-0.2% total cost impact in P&L.
- **Dependencies**: None.

## T0.4 — FII/DII Flow Ingestion
- **Why**: FII/DII net flows are a leading indicator for Nifty direction. Free, public data.
- **Scope**:
  - New module `core/market_flows.py`.
  - Scrape NSDL daily flow data (`https://www.nsdl.co.in/fpi/fpi_trends.aspx` or NSE alternative).
  - Store in `stocks/_market/fii_dii_flows.parquet` (market-level KB).
  - New feature in `MasterAgent` scoring: `fii_flow_tilt` (3-day rolling signed sum, normalized).
  - Expose in LLM prompt.
- **Acceptance**: `stocks/_market/fii_dii_flows.parquet` populated with last 60 days; value visible in master agent logs.
- **Dependencies**: None.

## T0.5 — NSE Option Chain Ingestion
- **Why**: OI, PCR, max pain indicate institutional positioning. Free via NSE endpoint.
- **Scope**:
  - New module `core/option_chain.py`.
  - Pull Nifty + BankNifty option chain daily post-market (from NSE API endpoint).
  - Also pull watchlist stocks that have F&O.
  - Compute derived metrics: PCR (ratio), max pain strike, OI concentration.
  - Store snapshots in `stocks/<symbol>/option_chain_history.parquet` or similar.
  - Expose to MasterAgent via regime agent or directly.
- **Acceptance**: Nightly snapshot committed. PCR and max pain values visible in logs.
- **Dependencies**: None. NSE headers and session management may be needed (some endpoints require cookies).

## T0.6 — Real NSE Corporate Announcements
- **Why**: News agent's `_scrape_nse_announcements` is an empty stub. Corporate filings catch events Yahoo misses.
- **Scope**:
  - Implement real scraper against NSE announcements endpoint.
  - Filter to watchlist symbols.
  - Pass to FinBERT for sentiment scoring (already wired via `ripple/sentiment_analyzer.py`).
  - Feed into `news_history.json` knowledge base.
- **Acceptance**: NSE-sourced announcements visible in news_history.json for at least one symbol, with FinBERT sentiment attached.
- **Dependencies**: None.

## T0.7 — Cloud VPS Setup
- **Why**: Automated trading requires 24/7 uptime. Laptop can't be the runtime.
- **Scope**:
  - Provision DigitalOcean t3.small (or AWS equivalent). ~$10/month.
  - Deploy framework via git + systemd service running `main.py --schedule`.
  - Set up Healthchecks.io heartbeat: scheduler pings every 5 min, alert if missed.
  - Logs rotated and pushed to S3 or left local with size limits.
- **Acceptance**: `--schedule` runs continuously for 72 hours without manual restart. Heartbeat alert works (test by stopping).
- **Dependencies**: VPS account, SSH key, Healthchecks.io free account.

## T0.8 — Telegram Alerts Enabled
- **Why**: Without alerts, a kill switch or emergency is invisible.
- **Scope**:
  - Create Telegram bot via BotFather.
  - Store token + chat ID in `.env`.
  - Set `telegram.enabled: true` in `config.yaml`.
  - Verify all alert types fire: trade_alert, exit_alert, emergency_alert, daily_summary.
  - Add a manual kill-switch command the bot can receive (stretch goal — can defer to Phase 2).
- **Acceptance**: All four alert types tested and received.
- **Dependencies**: Telegram account, bot token.

## Exit Criteria for Phase 0

All eight tasks have acceptance criteria met. Then run the full `--once` scheduler pipeline end-to-end and verify:
- No crashes
- All data sources populating KB
- Paper trades executing with realistic costs
- Alerts firing

After that, begin Phase 1 measurement (separate task breakdown when we get there).
