"""K1 Step 1.7 smoke: one Sonnet 4.5 call -> Langfuse trace + budget row.

Run: ``make smoke-llm`` or ``uv run python scripts/smoke_llm.py``.

Requires the following env vars to be set (in 2.0/.env or exported):

    ANTHROPIC_API_KEY
    LANGFUSE_PUBLIC_KEY
    LANGFUSE_SECRET_KEY
    POSTGRES_URL  (defaulted; the running docker compose stack must be up)

If any required env var is missing, prints a checklist and exits 0 (so the
make target does not fail in environments without credentials).

The script is structured so its body is callable as ``main()`` and unit-tested
without spawning a subprocess.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agora.platform.llm.client import AgoraLLM
from agora.platform.shared.settings import Settings

REQUIRED_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)

SMOKE_PM_ID = "smoke"
SMOKE_AGENT_ID = "smoke-script"


async def _ensure_smoke_pm(postgres_url: str) -> None:
    """INSERT a placeholder ``pms`` row so the FK on budget_events resolves."""
    engine = create_async_engine(postgres_url, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO pms (id, name, status, starting_capital_inr, prompt_path)
                    VALUES (:id, :name, :status, :capital, :prompt_path)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": SMOKE_PM_ID,
                    "name": SMOKE_PM_ID,
                    "status": "stopped",
                    "capital": 0,
                    "prompt_path": "/dev/null",
                },
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _check_postgres(postgres_url: str) -> bool:
    """SELECT 1 to verify Postgres is reachable; returns True on success."""
    engine = create_async_engine(postgres_url, future=True)
    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SELECT 1"))).scalar_one()
            return value == 1
    except Exception as e:
        print(f"  [postgres] {type(e).__name__}: {e}")
        return False
    finally:
        await engine.dispose()


async def _run_smoke(settings: Settings) -> int:
    if not await _check_postgres(settings.postgres_url):
        print(
            "Postgres is not reachable. "
            "Bring the stack up first: `make up && make db-migrate`. "
            "Exiting 0."
        )
        return 0

    await _ensure_smoke_pm(settings.postgres_url)

    llm = AgoraLLM(
        agent_id=SMOKE_AGENT_ID,
        pm_id=SMOKE_PM_ID,
        settings=settings,
    )
    result = await llm.call(
        model=settings.agora_default_reasoning_model,
        messages=[
            {
                "role": "user",
                "content": "Say 'hello, AGORA' and nothing else.",
            }
        ],
        max_tokens=32,
    )

    print("=" * 60)
    print(f"model:    {result.model}")
    print(f"content:  {result.content}")
    print(f"tokens:   in={result.tokens_in}  out={result.tokens_out}")
    print(f"cost:     ${result.cost_usd:.4f}")
    if result.langfuse_trace_id:
        print(f"trace_id: {result.langfuse_trace_id}")
        print(f"trace url: {settings.langfuse_host}/trace/{result.langfuse_trace_id}")
    else:
        print("trace_id: <none — Langfuse unconfigured or unreachable>")
    print("=" * 60)
    print()
    print("To inspect the budget row:")
    print(
        "  docker exec -it agora-postgres psql -U agora -d agora "
        '-c "SELECT id, pm_id, kind, amount_usd, ts FROM budget_events '
        'ORDER BY id DESC LIMIT 1;"'
    )
    return 0


def _missing_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]


def main(settings: Settings | None = None) -> int:
    """Entry point. Returns the desired process exit code.

    Always returns 0 in environments where credentials or services are
    missing — the smoke is informational, not a CI gate.
    """
    missing = _missing_env_vars()
    if missing:
        print("AGORA smoke (K1 Step 1.7) — skipped: missing env vars.")
        print()
        print("Required:")
        for name in REQUIRED_ENV_VARS:
            mark = "missing" if name in missing else "ok"
            print(f"  [{mark:>7}] {name}")
        print()
        print("Set them in 2.0/.env or export, then re-run `make smoke-llm`.")
        return 0

    settings = settings or Settings()
    return asyncio.run(_run_smoke(settings))


if __name__ == "__main__":
    sys.exit(main())
