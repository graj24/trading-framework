# Infrastructure & Operations Guide

## Architecture overview

```
You (CEO)
    │
    ▼
Multica Board  http://<MULTICA_EC2_IP>:3000
    │  assign tasks to PM agents
    ▼
Trading EC2 (<TRADING_EC2_IP>)  ← Multica daemon runs here
    ├── PM1 agent  (kiro-cli, executes in /app)
    ├── PM2 agent  (kiro-cli, executes in /app)
    ├── trading-daemon   → python main.py --schedule (24/7)
    ├── trading-api      → uvicorn api.main:app (port 8000)
    └── nginx            → port 80 → port 8000

Multica EC2 (<MULTICA_EC2_IP>)
    ├── multica-frontend  (port 3000)
    ├── multica-backend   (port 8080)
    └── multica-postgres  (port 5432)
```

---

## EC2 instances

| Instance | IP | Type | Purpose |
|---|---|---|---|
| trading-framework | <TRADING_EC2_IP> | m7i-flex.large (8GB) | Trading daemon + API + PM agents |
| multica-server | <MULTICA_EC2_IP> | t3.small (2GB) | Multica management platform |

SSH key for both: `~/.ssh/<your-key>.pem`

```bash
ssh -i ~/.ssh/<your-key>.pem ec2-user@<TRADING_EC2_IP>   # trading EC2
ssh -i ~/.ssh/<your-key>.pem ec2-user@<MULTICA_EC2_IP>  # multica EC2
```

---

## Access URLs

| Service | URL |
|---|---|
| Trading dashboard (React UI) | http://<TRADING_EC2_IP> |
| API docs (Swagger) | http://<TRADING_EC2_IP>/docs |
| Multica board | http://<MULTICA_EC2_IP>:3000 |
| Multica API | http://<MULTICA_EC2_IP>:8080 |

---

## Makefile tasks (run from repo root)

### Local development
| Command | What it does |
|---|---|
| `make run` | Run one analysis cycle locally |
| `make schedule` | Start the 24/7 scheduler locally |
| `make dashboard` | Open Streamlit dashboard |
| `make ui` | Start FastAPI + React UI locally |
| `make test` | Run pytest |

### EC2 operations
| Command | What it does |
|---|---|
| `make deploy` | Push latest code to EC2 + restart services |
| `make ssh` | SSH into trading EC2 |
| `make logs` | Tail live daemon logs on EC2 |
| `make status` | Check health of all 3 services on EC2 |
| `make restart` | Restart trading-api and trading-daemon on EC2 |

### Updating secrets
Updates the key in **both** your local `.env` and EC2's `.env`, then restarts EC2 services:
```bash
make update-key KEY=GROQ_API_KEY VALUE=your_new_key_here
```

---

## Day-to-day operations

### Push a code update
```bash
git push origin main   # triggers auto-deploy via GitHub Actions
# or manually:
make deploy
```

### Check if services are healthy
```bash
make status
```

### View live logs
```bash
make logs                                          # trading daemon
ssh -i ~/.ssh/<your-key>.pem ec2-user@<TRADING_EC2_IP> "tail -f /app/logs/api.log"
```

### Restart services
```bash
make restart
```

### Edit secrets / config on EC2
```bash
make ssh
nano /app/.env          # API keys
nano /app/config.yaml   # watchlist, capital, risk settings
sudo systemctl restart trading-daemon
```

---

## Portfolio Manager agents

See **`setup/MULTICA.md`** for the full Multica guide (adding PMs, assigning tasks, daemon ops, server ops).

Quick scoreboard:
```bash
sqlite3 /app/paper_trades.db "SELECT pm_id, SUM(pnl_inr) total_pnl FROM trades WHERE outcome!='open' GROUP BY pm_id ORDER BY total_pnl DESC;"
```

---

## First-time data setup (if stocks/ is missing)

```bash
make ssh
cd /app && source .venv/bin/activate

# Build knowledge bases (~15 min)
python -c "
import yaml; from agents.data_agent import DataAgent
cfg = yaml.safe_load(open('config.yaml'))
da = DataAgent(cfg)
[da.build_kb(s) for s in cfg['watchlist']]
"

# Train ML models
python models/ml_model.py train
python models/india_intraday_model.py fetch
python models/india_intraday_model.py train
```

---

## Cost

| Resource | Monthly cost |
|---|---|
| Trading EC2 (m7i-flex.large) | ~$70 |
| Multica EC2 (t3.small) | ~$15 |
| EBS volumes (2 × 20GB gp3) | ~$3 |
| Elastic IPs (2) | Free (attached) |
| **Total** | **~$88/month** |

Covered by AWS credits. Monitor at https://console.aws.amazon.com/billing/home#/credits

---

## AWS resource IDs

```
Trading EC2:   i-0ff6ea4482b95d3f6  (<TRADING_EC2_IP>)
Multica EC2:   <MULTICA_INSTANCE_ID>  (<MULTICA_EC2_IP>)
Trading EIP:   eipalloc-0adf311090a7cf7e4
Multica EIP:   eipalloc-055baded4671a7c80
Trading SG:    sg-025a71fd666c80cb8
Multica SG:    sg-074db92f6c94ff4e5
Region:        ap-south-1
SSH key:       ~/.ssh/<your-key>.pem
```
