#!/usr/bin/env bash
# Update a single key in /app/.env on EC2 and restart services.
# Usage: bash setup/update_env.sh <KEY> <VALUE> [path-to-pem]
#
# Example:
#   bash setup/update_env.sh GROQ_API_KEY gsk_abc123 ~/.ssh/<your-key>.pem
set -e

KEY="${1:?Usage: update_env.sh <KEY> <VALUE> [path-to-pem]}"
VALUE="${2:?Usage: update_env.sh <KEY> <VALUE> [path-to-pem]}"
PEM="${3:-~/.ssh/<your-key>.pem}"
HOST="ec2-user@<TRADING_EC2_IP>"

echo "==> Updating ${KEY} on ${HOST}..."

ssh -i "${PEM}" "${HOST}" "
  # Update or append the key in .env
  if grep -q '^${KEY}=' /app/.env; then
    sed -i 's|^${KEY}=.*|${KEY}=${VALUE}|' /app/.env
    echo 'Updated existing key.'
  else
    echo '${KEY}=${VALUE}' >> /app/.env
    echo 'Added new key.'
  fi

  # Restart services
  sudo systemctl restart trading-daemon trading-api
  echo 'Services restarted.'
"

echo "✅ Done."
