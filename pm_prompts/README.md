# Portfolio Manager Prompts

Each PM is an autonomous LLM agent competing to generate the highest returns on the Indian equity market. They share the same codebase, the same data, and the same trade ledger — but each has full freedom to build their own strategy.

## Files

| File | Purpose |
|---|---|
| `TEMPLATE.md` | Generic system prompt for any new PM — covers identity, framework access, freedom, and scoreboard |
| `PM1.md` | PM1's inherited strategy (handoff from human PM to AI PM) |
| `PM2.md` | PM2's competitive context vs PM1 |

## Creating a new PM

1. Copy `TEMPLATE.md` — fill in `{PM_NAME}` and `{id}`
2. Add a competitor context section (show it the other PMs' strategies and their weaknesses)
3. Save as `PM<N>.md`
4. Give the full prompt (`TEMPLATE.md` + `PM<N>.md`) to the LLM agent

## How to use these prompts

Concatenate the template with the PM-specific file:

```
[TEMPLATE.md content]

---

[PM<N>.md content]
```

That's the full system prompt for the agent.

## Scoreboard

```bash
sqlite3 paper_trades.db "
SELECT pm_id, COUNT(*) trades, SUM(pnl_inr) total_pnl,
       ROUND(100.0 * SUM(CASE WHEN pnl_inr > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win_rate_pct
FROM trades WHERE outcome != 'open'
GROUP BY pm_id ORDER BY total_pnl DESC;
"
```
