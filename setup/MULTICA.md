# Multica — Agent Management Platform

Multica is the management layer for PM agents. You (CEO) assign tasks from the Multica board; agents execute them on the trading EC2.

## Access

| | |
|---|---|
| Board UI | http://13.232.42.85:3000 |
| Backend API | http://13.232.42.85:8080 |
| SSH | `ssh -i ~/.ssh/trading-key.pem ec2-user@13.232.42.85` |

---

## Architecture

```
Multica EC2 (13.232.42.85)
├── multica-frontend   port 3000  (Next.js board UI)
├── multica-backend    port 8080  (Go API + WebSocket)
└── multica-postgres   port 5432  (PostgreSQL + pgvector)

Trading EC2 (13.206.3.62)
└── multica daemon  ←→  connects to 13.232.42.85:8080
    ├── kiro-cli  (primary agent executor)
    └── claude    (fallback)
```

---

## Current PM agents

| Agent | Provider | System prompt |
|---|---|---|
| PM1 | kiro | `pm_prompts/PM1_full_prompt.md` |
| PM2 | kiro | `pm_prompts/PM2_full_prompt.md` |

---

## As CEO — day-to-day

**Assign a task to a PM:**
1. Go to http://13.232.42.85:3000
2. Create an issue (describe what you want the PM to do)
3. Assign it to PM1 or PM2
4. The agent picks it up, executes on the trading EC2 in `/app`, and reports back

**Examples of tasks you can assign:**
- "Analyse this week's losing trades and suggest strategy improvements"
- "Add a new data source for FII/DII flows"
- "Reduce the confidence threshold from 60% to 55% and backtest the impact"
- "Build a short-selling capability"

**Check P&L scoreboard:**
```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62
sqlite3 /app/paper_trades.db "
SELECT pm_id, COUNT(*) trades, SUM(pnl_inr) total_pnl,
       ROUND(100.0 * SUM(CASE WHEN pnl_inr > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) win_rate
FROM trades WHERE outcome != 'open'
GROUP BY pm_id ORDER BY total_pnl DESC;
"
```

---

## Adding a new PM

1. Create the prompt:
```bash
# Create PM3-specific context file
nano pm_prompts/PM3.md

# Generate the full copy-paste prompt
cat pm_prompts/TEMPLATE.md pm_prompts/PM3.md > pm_prompts/PM3_full_prompt.md
git add pm_prompts/ && git commit -m "feat: add PM3 prompt" && git push
```

2. In Multica: **Settings → Agents → New Agent**
   - Name: `PM3`
   - Runtime: trading EC2
   - Provider: `kiro`
   - System prompt: paste contents of `pm_prompts/PM3_full_prompt.md`

---

## Multica daemon (on trading EC2)

The daemon connects the trading EC2 to the Multica server and listens for tasks.

```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62

multica daemon status   # check if running
multica daemon start    # start
multica daemon stop     # stop
```

**If daemon stops unexpectedly:**
```bash
cat ~/.multica/daemon.log   # check error
multica daemon start
```

**If daemon loses connection to Multica server:**
```bash
multica daemon stop
multica setup self-host --server-url http://13.232.42.85:8080
# frontend URL: http://13.232.42.85:3000
# login with mul_ token from Settings → Access Tokens
multica daemon start
```

**Make daemon survive reboots** (run once on trading EC2):
```bash
cat > ~/.config/systemd/user/multica-daemon.service << 'EOF'
[Unit]
Description=Multica Agent Daemon
After=network.target

[Service]
ExecStart=/usr/local/bin/multica daemon start --foreground
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
systemctl --user enable multica-daemon
systemctl --user start multica-daemon
```

---

## Multica server operations (on Multica EC2)

```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.232.42.85

# Check all containers
docker ps

# Restart everything
docker compose -f ~/docker-compose.selfhost.yml --env-file ~/.env restart

# View backend logs (includes email verification codes)
docker logs multica-backend-1 -f

# Stop / start
docker compose -f ~/docker-compose.selfhost.yml --env-file ~/.env down
docker compose -f ~/docker-compose.selfhost.yml --env-file ~/.env up -d
```

**If you lose access / forget password:**
```bash
docker logs multica-backend-1 2>&1 | grep -i "verification\|code" | tail -5
```
The verification code prints to the backend log when email is not configured.

---

## Kiro CLI on trading EC2

Kiro is the agent executor. It needs to stay authenticated.

```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62

kiro-cli whoami          # check auth status
kiro-cli login           # re-authenticate (opens browser URL)
kiro-cli --version       # check version
```

**Update Kiro CLI:**
```bash
curl -fsSL https://cli.kiro.dev/install | bash
