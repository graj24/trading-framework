# AGORA Makefile — all targets relative to 2.0/.
# Stubs print TODO and exit non-zero so that CI cannot accidentally pass on un-shipped work.

.DEFAULT_GOAL := help

.PHONY: help install lint fmt typecheck test test-cov ci-local \
        precommit-install precommit \
        up down logs health \
        db-migrate db-shell \
        api worker dashboard smoke-llm \
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
	@echo "  up/down/logs       (TODO: K1 Step 1.2)"
	@echo "  health             (TODO: K1 Step 1.4)"
	@echo "  db-migrate/db-shell (TODO: K1 Step 1.3)"
	@echo "  api                (TODO: K1 Step 1.4)"
	@echo "  worker             (TODO: K1 Step 1.6)"
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
	@echo "TODO: K1 Step 1.2 — docker compose up"; exit 1

down:
	@echo "TODO: K1 Step 1.2 — docker compose down"; exit 1

logs:
	@echo "TODO: K1 Step 1.2 — docker compose logs -f"; exit 1

health:
	@echo "TODO: K1 Step 1.4 — curl /api/health"; exit 1

db-migrate:
	@echo "TODO: K1 Step 1.3 — alembic upgrade head"; exit 1

db-shell:
	@echo "TODO: K1 Step 1.3 — psql into agora db"; exit 1

api:
	@echo "TODO: K1 Step 1.4 — uvicorn agora.platform.control_plane.app:app"; exit 1

worker:
	@echo "TODO: K1 Step 1.6 — agora-worker run"; exit 1

dashboard:
	@echo "TODO: K1 Step 1.8 — pnpm --dir dashboard dev"; exit 1

smoke-llm:
	@echo "TODO: K1 Step 1.7 — scripts/smoke_llm.py"; exit 1

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build
