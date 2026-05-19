# Set EC2_IP in your environment or pass it directly: make deploy EC2_IP=1.2.3.4
EC2_IP   ?= $(shell echo $${EC2_IP:-YOUR_EC2_IP})
EC2_USER = ec2-user
PEM      = ~/.ssh/id_ed25519
SSH      = ssh -i $(PEM) $(EC2_USER)@$(EC2_IP)

# ── Local ─────────────────────────────────────────────────────────────────────

run:
	source .venv/bin/activate && python main.py

schedule:
	source .venv/bin/activate && python main.py --schedule

dashboard:
	source .venv/bin/activate && streamlit run scripts/dashboard.py

ui:
	bash start_ui.sh

test:
	source .venv/bin/activate && python -m pytest

# ── EC2 ───────────────────────────────────────────────────────────────────────

deploy:
	bash setup/deploy.sh $(EC2_IP) $(PEM)

ssh:
	ssh -i $(PEM) $(EC2_USER)@$(EC2_IP)

logs:
	$(SSH) "tail -f /app/logs/daemon.log"

status:
	$(SSH) "sudo systemctl status trading-api trading-daemon nginx --no-pager"

restart:
	$(SSH) "sudo systemctl restart trading-api trading-daemon"

# Usage: make update-key KEY=GROQ_API_KEY VALUE=your_new_key
update-key:
	@bash setup/update_env.sh $(KEY) $(VALUE) $(PEM)
	@sed -i '' 's|^$(KEY)=.*|$(KEY)=$(VALUE)|' .env 2>/dev/null || echo "$(KEY)=$(VALUE)" >> .env
	@echo "✅ Updated $(KEY) on EC2 and locally."

.PHONY: run schedule dashboard ui test deploy ssh logs status restart update-key
