# AWS Hosting — Trading Framework

## What's running where

```
Your Mac  ──────────────────────────────────────────────────────────
  ~/.ssh/trading-key.pem   (SSH key to access the server)
  setup/deploy.sh          (script to push code updates)

AWS (ap-south-1 / Mumbai)  ─────────────────────────────────────────
  EC2: m7i-flex.large  (2 vCPU, 8GB RAM)
  Elastic IP: 13.206.3.62  (fixed — won't change on reboot)
  EBS: 20GB gp3  (all code, data, SQLite live here)
  Security group: port 22 (SSH) + port 80 (HTTP) open
```

## What runs on the server

Three systemd services, all auto-start on reboot:

| Service | What it does | Command |
|---|---|---|
| `trading-api` | FastAPI backend + serves React UI on port 8000 | `uvicorn api.main:app` |
| `trading-daemon` | 24/7 trading scheduler (IST market hours) | `python main.py --schedule` |
| `nginx` | Reverse proxy — forwards port 80 → port 8000 | — |

## Access

| | |
|---|---|
| App (React UI) | http://13.206.3.62 |
| API docs (Swagger) | http://13.206.3.62/docs |
| SSH into server | `ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62` |

## File layout on server

```
/app/                        ← all project files live here
├── .env                     ← secrets (GROQ_API_KEY etc.) — never in git
├── config.yaml              ← trading config (watchlist, risk, schedule)
├── paper_trades.db          ← SQLite trade ledger
├── stocks/                  ← per-stock knowledge bases (built by DataAgent)
├── models/stocks_1h/        ← 1h candle data + trained ML model
├── logs/
│   ├── daemon.log           ← trading scheduler logs
│   ├── api.log              ← FastAPI logs
│   └── trading.log          ← main pipeline logs
└── frontend/dist/           ← built React app (served by FastAPI)
```

## Day-to-day operations

### Push a code update from your Mac
```bash
bash setup/deploy.sh 13.206.3.62 ~/.ssh/trading-key.pem
```
This rsyncs code (skipping `.env`, `stocks/`, `paper_trades.db`), rebuilds the frontend, and restarts services.

### Check if services are healthy
```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62
sudo systemctl status trading-api trading-daemon nginx
```

### View live logs
```bash
# Trading scheduler
tail -f /app/logs/daemon.log

# FastAPI
tail -f /app/logs/api.log

# Or via journalctl
sudo journalctl -u trading-daemon -f
sudo journalctl -u trading-api -f
```

### Restart a service
```bash
sudo systemctl restart trading-api
sudo systemctl restart trading-daemon
```

### Edit secrets / config
```bash
nano /app/.env          # API keys
nano /app/config.yaml   # watchlist, capital, risk settings
sudo systemctl restart trading-daemon  # pick up config changes
```

## First-time data setup (already done if stocks/ exists)

If you ever need to rebuild stock data from scratch:
```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62
cd /app && source .venv/bin/activate

# Build knowledge bases (~15 min for full watchlist)
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

## Cost

~$70/month (m7i-flex.large on-demand in ap-south-1).  
Free tier eligible — covered by AWS credits.

To stop billing if you want to pause: stop (not terminate) the instance from AWS Console.  
The Elastic IP costs ~$3.60/month while the instance is stopped.

## AWS resource IDs (for reference)

```
Instance ID  : i-0ff6ea4482b95d3f6
Elastic IP   : 13.206.3.62
Alloc ID     : eipalloc-0adf311090a7cf7e4
Security Group: sg-025a71fd666c80cb8
Region       : ap-south-1
SSH key      : ~/.ssh/trading-key.pem
```
