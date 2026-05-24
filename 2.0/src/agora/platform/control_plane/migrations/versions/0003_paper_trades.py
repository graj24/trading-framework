"""paper_trades — broker trade ledger

Revision ID: 0003_paper_trades
Revises: 0002_pm_workflow_id
Create Date: 2026-01-15 00:00:00

K3 Step 3.4 schema per plan/01-KEYSTONE.md §5. Open trades carry
``outcome='open'``; the broker / EOD closer / strategy / operator
transitions them to one of ``sl_hit``, ``target_hit``, ``eod_close``,
``manual``, ``signal_exit`` on close.

The vocabulary is enforced in Python via :data:`agora.platform.control_plane.trade_repo.TradeOutcome`
(a ``Literal``). We deliberately keep the column as plain ``TEXT`` for
K3 — adding a CHECK constraint here would mean a follow-up migration
every time the vocabulary grows (``signal_exit`` was added in the K3
post-audit pass without a schema migration; that is the point). K8
hardening can layer the CHECK in once the set is frozen.

Indexes: a plain index on ``pm_id`` (for the dashboard's PM-positions
card) plus a composite ``(pm_id, outcome)`` for the K7 leaderboard
query (``SUM(pnl_inr) WHERE outcome != 'open' GROUP BY pm_id``).
Indexing it now is cheap and saves a follow-up migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_paper_trades"
down_revision: str | None = "0002_pm_workflow_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "pm_id",
            sa.Text(),
            sa.ForeignKey("pms.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(), nullable=True),
        sa.Column("entry_ts", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("stop_loss", sa.Numeric(), nullable=True),
        sa.Column("target", sa.Numeric(), nullable=True),
        sa.Column("exit_price", sa.Numeric(), nullable=True),
        sa.Column("exit_ts", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("pnl_inr", sa.Numeric(), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(), nullable=True),
        sa.Column("strategy_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("paper_trades_pm_id_idx", "paper_trades", ["pm_id"])
    op.create_index("paper_trades_pm_id_outcome_idx", "paper_trades", ["pm_id", "outcome"])


def downgrade() -> None:
    op.drop_index("paper_trades_pm_id_outcome_idx", table_name="paper_trades")
    op.drop_index("paper_trades_pm_id_idx", table_name="paper_trades")
    op.drop_table("paper_trades")
