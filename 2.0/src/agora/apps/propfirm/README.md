# propfirm app

Indian equity prop-firm app. Wraps NautilusTrader against a simulated
NSE venue, plus the AGORA-shaped market-data and broker tools that the
PM agents will pull from in K3.5+.

## Layout

| Path | Purpose | Keystone step |
|---|---|---|
| `trading/engine.py`      | `BacktestEngine` factory, `NSE_PAPER` venue id | 3.1 |
| `trading/instruments.py` | NSE `Equity` constructor, `NIFTY_50_SYMBOLS` | 3.1 |
| `trading/smoke.py`       | Synthetic-bar end-to-end smoke (`make trading-smoke`) | 3.1 |
| `data/nse.py`            | `MarketDataAdapter` + `ParquetMarketData` | 3.2 |

Future work: `seed_strategies/` (3.3), `broker.py` integration (3.4),
trading-cycle activity wiring into `PMSupervisor` (3.5).

## Drift from the plan: daily bars, not 1-minute

`plan/01-KEYSTONE.md` §5 Step 3.1 mentions 1-minute bars. The legacy
repo's per-stock parquet files (`stocks/<SYM>/price_history.parquet`,
the source of truth we point `ParquetMarketData` at) are **daily** bars.

Two paths considered:

1. **Adapt to daily bars** (taken). The 3.3 seed strategy is a 20-day /
   50-day SMA crossover with ATR(14) stops. On daily bars, that reduces
   to a vanilla SMA-cross strategy. The plumbing exercised — bar
   subscription, indicator updates, risk gates, broker submit, trade
   recording — is identical. `BarType` strings carry `1-DAY-LAST-EXTERNAL`
   instead of `1-MINUTE-LAST-EXTERNAL`. Documented at the call sites.

2. Resample or fetch real intraday data. Adds complexity (yfinance fetch
   loop or a paid feed) without changing the K3 plumbing exercise.

Path 1 is the right call for K3 ("deliberately simple, exercise the
plumbing"). When the prod adapter lands (post-K3), it can serve
1-minute bars and the seed strategy gets re-tuned. The interfaces don't
change.

## Venue id: `NSEPAPER`, no hyphen

NautilusTrader's `BacktestExecClient` derives the account issuer by
splitting the venue id on `-` and asserts the issuer matches the venue.
A hyphenated `NSE-PAPER` blows that assertion. We use `NSEPAPER`
everywhere — venue id, `BarType` strings, `InstrumentId` venues.

The 3.0 recon notes mentioned `NSE-PAPER`; that was an early sketch
before this constraint surfaced during 3.1 implementation.

## Smoke

```
make trading-smoke
```

Builds the engine, adds RELIANCE, generates 100 random-walk daily bars,
runs them through a no-op `Strategy` subclass that counts `on_bar`
calls, prints `OK — 100 bars processed`.

## Local market data

```
uv run python -m agora.apps.propfirm.data.nse RELIANCE 5
```

Prints the last 5 daily `Bar` objects for RELIANCE. Reads from the
legacy `<repo-root>/stocks/<SYM>/price_history.parquet`. Override the
root via `AGORA_STOCKS_ROOT=/path/to/stocks` if you don't have that
checkout side-by-side. The CLI exits with a clear error if neither the
explicit nor the default path resolves.

## PM config: source of truth

Each PM has two config-shaped surfaces:

- `pms.config` (Postgres JSONB column) — populated as `{}` by K2's
  spawn endpoint and never written again.
- `pms/<pm_id>/config.yaml` (workspace file) — written by the
  provision activity at PM spawn (K2.1), read by the workflow at
  start (K2 post-audit/3) for cadence values and by the trading
  cycle (K3.5) for the watchlist.

**The workspace YAML is the source of truth.** The DB column is
deprecated but kept for now so existing PMs don't need a migration.
K4+ tools that need to evolve config (cadence, watchlist, strategy
selection) write to the YAML; the next workflow restart picks up
the change.

The DB column will be dropped in K8 hardening once we're confident
nothing reads from it.
