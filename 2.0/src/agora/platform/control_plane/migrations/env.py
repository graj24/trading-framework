"""Alembic environment.

Resolves the database URL from `POSTGRES_URL` (or the alembic.ini fallback) and
strips the `+asyncpg` driver suffix so alembic can run synchronously through
psycopg2/psycopg. The application code itself uses asyncpg at runtime; alembic
just needs a sync DBAPI.

Operating mode is always "online" — we connect to a real database for both
upgrade and downgrade. There is no offline SQL-script mode here.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No declarative metadata yet — we use raw `op.create_table(...)` in revisions.
# Once the codebase grows SQLAlchemy models, point target_metadata at them.
target_metadata = None


def _resolve_url() -> str:
    """Return a sync-driver Postgres URL for alembic."""
    url = os.environ.get("POSTGRES_URL") or config.get_main_option("sqlalchemy.url") or ""
    if not url:
        raise RuntimeError(
            "POSTGRES_URL is not set. Export it (or set sqlalchemy.url in alembic.ini) "
            "before running alembic."
        )
    # The app uses postgresql+asyncpg://...; alembic needs a sync driver.
    # We standardize on psycopg3 (postgresql+psycopg://) for the alembic engine.
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_online() -> None:
    """Run migrations against a real database."""
    url = _resolve_url()
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
