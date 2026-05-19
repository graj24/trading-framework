#!/usr/bin/env bash
# Deploy latest code from your Mac to EC2.
# Usage: bash setup/deploy.sh <elastic-ip> [path-to-pem]
#
# Example:
#   bash setup/deploy.sh 13.233.45.67 ~/.ssh/trading-key.pem
set -e

EC2_IP="${1:?Usage: deploy.sh <elastic-ip> [path-to-pem]}"
PEM="${2:-~/.ssh/id_ed25519}"
REMOTE="ec2-user@${EC2_IP}"

echo "==> Syncing code to ${REMOTE}:/app ..."
rsync -avz --progress \
  -e "ssh -i ${PEM} -o StrictHostKeyChecking=no" \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude 'stocks/' \
  --exclude 'paper_trades.db' \
  --exclude 'paper_trades.db-shm' \
  --exclude 'paper_trades.db-wal' \
  --exclude 'logs/' \
  --exclude 'frontend/dist' \
  --exclude '.env' \
  ./ "${REMOTE}:/app/"

echo "==> Building frontend on server ..."
ssh -i "${PEM}" "${REMOTE}" "cd /app/frontend && npm ci --silent && npm run build"

echo "==> Restarting services ..."
ssh -i "${PEM}" "${REMOTE}" "sudo systemctl restart trading-api && sudo systemctl restart trading-daemon"

echo ""
echo "✅ Deployed. App running at http://${EC2_IP}"
