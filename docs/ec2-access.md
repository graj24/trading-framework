# EC2 Access for Collaborators

EC2 host: `13.206.3.62`  
User: `ec2-user`

---

## For your collaborator (do this first)

**Step 1 — Generate a keypair on your machine**

```bash
ssh-keygen -t ed25519 -C "your-name"
```

Press Enter to accept the default path (`~/.ssh/id_ed25519`). Set a passphrase or leave blank.

**Step 2 — Copy your public key and send it**

```bash
cat ~/.ssh/id_ed25519.pub
```

Send the output (one line starting with `ssh-ed25519 AAAA...`) to the repo owner (Gaurav). Do not send the private key (`id_ed25519`).

**Step 3 — Once Gaurav confirms he's added it, connect**

```bash
ssh ec2-user@13.206.3.62
```

No PEM file needed. SSH uses your private key automatically.

---

## For Gaurav (repo owner) — adding a collaborator

**Step 1 — SSH into EC2**

```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62
```

**Step 2 — Add their public key**

```bash
echo "ssh-ed25519 AAAA...their-full-public-key..." >> ~/.ssh/authorized_keys
```

Paste their exact public key (the line they sent you from Step 2 above).

**Step 3 — Verify it was added**

```bash
tail -5 ~/.ssh/authorized_keys
```

You should see their key at the bottom.

**Step 4 — Tell them to connect** — they're good to go.

---

## Revoking access

To remove a collaborator's access:

```bash
ssh -i ~/.ssh/trading-key.pem ec2-user@13.206.3.62
nano ~/.ssh/authorized_keys
# Delete the line with their key, save and exit
```

---

## App directory

The trading framework lives at `/app`. After connecting:

```bash
cd /app
# Check service status
sudo systemctl status trading-api pm-strategist@1 pm-strategist@2 price-feed
# View logs
journalctl -u pm-strategist@1 -f
```
