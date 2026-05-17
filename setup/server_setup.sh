#!/usr/bin/env bash
# Run this ONCE on a fresh EC2 instance (Amazon Linux 2023) as ec2-user.
# Usage: bash setup/server_setup.sh
set -e

echo "=== Trading Framework — Server Setup ==="

# 1. System packages
sudo dnf update -y
sudo dnf install -y git nginx python3.11 python3.11-pip python3.11-devel gcc

# 2. Node.js (for building frontend)
curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
sudo dnf install -y nodejs

# 3. App directory (code is already here if you rsync'd first)
sudo mkdir -p /app
sudo chown ec2-user:ec2-user /app

# 4. Python venv
cd /app
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install "uvicorn[standard]" fastapi

# 5. Build React frontend
cd /app/frontend
npm ci
npm run build
cd /app

# 6. Create logs dir
mkdir -p logs

# 7. Systemd services
sudo cp setup/trading-daemon.service /etc/systemd/system/
sudo cp setup/trading-api.service    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-daemon trading-api

# 8. Nginx
sudo cp setup/nginx.conf /etc/nginx/conf.d/trading.conf
# Remove default nginx config if present
sudo rm -f /etc/nginx/conf.d/default.conf
sudo systemctl enable nginx

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Create /app/.env with your secrets (GROQ_API_KEY etc.)"
echo "     cp /app/.env.example /app/.env && nano /app/.env"
echo ""
echo "  2. Build stock knowledge bases:"
echo "     source /app/.venv/bin/activate"
echo "     python -c \""
echo "     import yaml; from agents.data_agent import DataAgent"
echo "     cfg = yaml.safe_load(open('config.yaml'))"
echo "     da = DataAgent(cfg)"
echo "     [da.build_kb(s) for s in cfg['watchlist']]"
echo "     \""
echo ""
echo "  3. Start services:"
echo "     sudo systemctl start trading-api trading-daemon nginx"
echo ""
echo "  4. Check status:"
echo "     sudo systemctl status trading-api trading-daemon"
