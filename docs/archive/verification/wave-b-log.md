# Wave B — Medium items

> Started: 2026-05-16 19:00 IST. Each item ~30m–2h.

## Plan

| Step | Item                                                          | Effort | Status   |
|------|---------------------------------------------------------------|--------|----------|
| B.1  | core/symbols.py canonical NIFTY 50 list                       | 1h     | ✅ done  |
| B.2  | Rename intraday_* → tech_5m_* / ml_1h_*                       | 30m    | ✅ done  |
| B.3  | C6 — per-agent timing in logger                                | 30m    | ✅ done  |
| B.4  | core/config.py singleton                                       | 1.5h   | ✅ done  |
| B.5  | C9 — SQLite WAL                                                | 30m    | ✅ done  |
| B.6  | B3 — apply learned weights in rule decision                   | 1h     | ✅ done  |
| B.7  | B8 — BSE scrip code lookup                                     | 1h     | ⏸ deferred (needs lookup table) |
| B.8  | C4 — retry/backoff for network calls                          | 1h     | ✅ done  |
| B.9  | ThreadPoolExecutor over symbols                                | 1h     | ✅ done  |
| B.10 | Holiday-aware F&O expiry (B11)                                 | 30m    | ✅ done  |

**Wave B done. 9 of 10 items shipped, 1 deferred (B.7 needs offline scrip-code data).**
70 unit tests, all green. Wall time: ~3.5 h.
