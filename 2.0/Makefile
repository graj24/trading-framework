# AGORA Makefile — all targets relative to 2.0/.
# Stubs print TODO and exit non-zero so that CI cannot accidentally pass on un-shipped work.

.DEFAULT_GOAL := help

.PHONY: help install lint fmt typecheck test test-cov ci-local \
        precommit-install precommit \
        up down logs health \
        db-migrate db-downgrade db-shell \
        api worker hello-smoke dashboard smoke-llm \
        clean

help:
	@echo "AGORA — make targets"
	@echo ""
	@echo "  install            uv sync --all-groups"
	@echo "  lint               ruff check + format --check"
	@echo "  fmt                ruff format + ruff --fix"
	@echo "  typecheck          mypy"
	@echo "  test               pytest"
	@echo "  test-cov           pytest with coverage"
	@echo "  ci-local           lint + typecheck + test"
	@echo "  precommit-install  install pre-commit hooks"
	@echo "  precommit          run pre-commit on all files"
	@echo "  up/down/logs       docker compose stack (postgres, temporal, qdrant, letta)"
	@echo "  health             curl /api/health (uses jq if available)"
	@echo "  db-migrate         alembic upgrade head"
	@echo "  db-downgrade       alembic downgrade -1"
	@echo "  db-shell           psql into the agora DB inside the container"
	@echo "  api                uvicorn (factory mode) on :8000"
	@echo "  worker             run the Temporal worker on task_queue=agora"
	@echo "  hello-smoke        run agora-cli hello world (worker must be up)"
	@echo "  dashboard          (TODO: K1 Step 1.8)"
	@echo "  smoke-llm          (TODO: K1 Step 1.7)"
	@echo "  clean              remove caches and venv"

install:
	uv sync --all-groups

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

test:
	uv run pytest

test-cov:
	uv run pytest --cov=agora --cov-report=term-missing

ci-local: lint typecheck test

precommit-install:
	uv run pre-commit install

precommit:
	uv run pre-commit run --all-files

up:
	docker compose -f infra/docker-compose.yml up -d
	@echo "Waiting for services to register..."
	@docker compose -f infra/docker-compose.yml ps

down:
	docker compose -f infra/docker-compose.yml down

logs:
	docker compose -f infra/docker-compose.yml logs -f

health:
	@if command -v jq >/dev/null 2>&1; then \
		curl -s localhost:8000/api/health | jq .; \
	else \
		curl -s localhost:8000/api/health; \
	fi

db-migrate:
	POSTGRES_URL=$${POSTGRES_URL:-postgresql+asyncpg://agora:agora@localhost:5432/agora} \
		uv run alembic upgrade head

db-downgrade:
	POSTGRES_URL=$${POSTGRES_URL:-postgresql+asyncpg://agora:agora@localhost:5432/agora} \
		uv run alembic downgrade -1

db-shell:
	# psql does not understand +asyncpg; use a plain Postgres URL.
	docker exec -it agora-postgres psql -U agora -d agora

api:
	uv run uvicorn agora.platform.control_plane.app:create_app \
		--factory --host 0.0.0.0 --port 8000 --reload

worker:
	uv run python -m agora.platform.workers.main

hello-smoke:
	uv run agora-cli hello world

dashboard:
	@echo "TODO: K1 Step 1.8 — pnpm --dir dashboard dev"; exit 1

smoke-llm:
	uv run python scripts/smoke_llm.py

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build
