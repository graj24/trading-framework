"""initial schema (pms, agents, runs, budget_events, kill_switch, mode_overrides)

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

Tables defined per plan/01-KEYSTONE.md §3 Step 1.3.

The kill_switch row is seeded at id=1 (singleton constraint). Tables for
tasks/prs/trades/journals are deferred to their respective keystones.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pms",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("starting_capital_inr", sa.Numeric(), nullable=False),
        sa.Column(
            "spawned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("stopped_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("prompt_path", sa.Text(), nullable=False),
        sa.Column(
            "config",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "pm_id",
            sa.Text(),
            sa.ForeignKey("pms.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_agent_id",
            sa.Text(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "config",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Text(),
            sa.ForeignKey("agents.id"),
            nullable=True,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("langfuse_trace", sa.Text(), nullable=True),
    )

    op.create_table(
        "budget_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "pm_id",
            sa.Text(),
            sa.ForeignKey("pms.id"),
            nullable=True,
        ),
        sa.Column(
            "ts",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
    )

    op.create_table(
        "kill_switch",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("activated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="kill_switch_singleton"),
    )
    # Seed the singleton row.
    op.execute("INSERT INTO kill_switch (id, active) VALUES (1, FALSE)")

    op.create_table(
        "mode_overrides",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Reverse order so foreign keys unwind cleanly.
    op.drop_table("mode_overrides")
    op.drop_table("kill_switch")
    op.drop_table("budget_events")
    op.drop_table("runs")
    op.drop_table("agents")
    op.drop_table("pms")
