"""Pydantic Settings for AGORA control plane.

Reads from process environment with `.env` as a fallback. All names are
case-insensitive (Pydantic's default). Empty-string defaults are deliberate for
secrets — they let the app boot without keys but signal "degraded" on the
health endpoint.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["json", "human"]


class Settings(BaseSettings):
    """Process-wide AGORA configuration."""

    # Postgres — the app uses the asyncpg driver; alembic re-writes to psycopg.
    postgres_url: str = "postgresql+asyncpg://agora:agora@localhost:5432/agora"

    # Temporal cluster.
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"

    # Langfuse Cloud — secrets default to empty so the app boots without keys.
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Letta server (memory).
    letta_host: str = "http://localhost:8283"

    # Qdrant vector store.
    qdrant_host: str = "http://localhost:6333"

    # Logging — "human" by default for dev; CI/prod set AGORA_LOG_FORMAT=json.
    log_format: LogFormat = "human"

    # Workspace root for /pms/<id>/ trees. Empty = use the repo dir at runtime.
    workspace_root: str = ""

    # Default LLM models (override per-agent at runtime). Names follow
    # litellm's "<provider>/<model>" routing convention.
    agora_default_reasoning_model: str = "anthropic/claude-sonnet-4-5"
    agora_default_cheap_model: str = "anthropic/claude-haiku-4-5"

    # K2 Step 2.5 — worker process publishes activity events via
    # POST /api/internal/events. Empty token disables the route (503),
    # which is the correct local-dev behavior until an operator opts
    # in. Worker reads the same env to authenticate; both sides see
    # the same Settings, so config drift is one less thing to debug.
    internal_event_token: str = ""
    agora_api_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — call this from FastAPI dependencies, etc."""
    return Settings()
