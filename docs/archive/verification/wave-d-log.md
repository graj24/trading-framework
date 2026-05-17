# Wave D — ML Promotion Gate + Anomaly Alerts

**Date**: 2026-05-16  
**Tests before**: 78  
**Tests after**: 93  
**Regressions**: 0

---

## D.1 — ML promotion gate (`ml_model.py`, `india_intraday_model.py`)

**Problem**: `train()` unconditionally overwrote `model.pkl`, so a bad training run (noisy data, fewer stocks) could silently degrade the live model.

**Fix**:
- Added `MIN_AUC_DELTA = -0.02` constant (new model must not be more than 2 pp worse than incumbent).
- Added `_incumbent_auc(X_val, y_val)` — reads stored `auc` field from pickle; falls back to re-evaluation on the val slice for old models that lack the field.
- Added `_save_if_better(model, features, new_auc, X_val, y_val) -> bool` — compares new AUC against incumbent + delta; saves and returns `True` if promoted, logs rejection and returns `False` otherwise.
- Replaced the direct `pickle.dump` at the end of `train()` in both files with `_save_if_better(...)`.
- Saved pickle now always includes `{"model": ..., "features": ..., "auc": <mean_cv_auc>}`.

**Tests**: `tests/test_p2_ml_promotion_gate.py` — 8 tests covering:
- No incumbent → always saves
- Better model → saves, payload includes new AUC
- Worse model → rejected, old file unchanged
- Boundary (exactly at delta) → accepted
- Both daily and intraday model variants

---

## D.2 — Anomaly alerts (`core/scheduler.py`)

**Problem**: Silent failures — if the NSE pre-open API returned nothing, the scheduler logged a warning but sent no Telegram alert. Similarly, P&L approaching the daily loss limit had no early warning.

**Fix** (both in `core/scheduler.py`):

1. **`job_preopen_scan`**: after `PreOpenMonitor.scan()`, if `result["all_preopen"]` is empty, sends:
   ```
   ⚠️ ANOMALY: Pre-open scan returned 0 results — NSE API may be down or market is closed.
   ```

2. **`job_monitor_positions`**: after `monitor_positions()`, reads `today_pnl_pct` and compares against `-(max_loss_per_day_pct * 0.75)`. If breached, sends:
   ```
   ⚠️ P&L ALERT: Today's P&L is X.XX% (limit: -Y.Y%). Approaching daily loss limit.
   ```
   Also imports `today_pnl_pct` from `agents.execution_agent` (was missing from the import list).

**Tests**: `tests/test_p2_anomaly_alerts.py` — 7 tests covering:
- Zero pre-open results → ANOMALY alert fires
- Normal results → no ANOMALY alert
- Buy signal still sent when present
- P&L at 75% of limit → alert fires
- P&L within safe range → no alert
- Positive P&L → no alert
- Tighter config limit → alert fires earlier
