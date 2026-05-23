"""pms.workflow_id (nullable) — populated by Step 2.2 when the
PMSupervisor workflow is started.

Revision ID: 0002_pm_workflow_id
Revises: 0001_initial
Create Date: 2026-01-08 00:00:00

K2 Step 2.1 only inserts the row and provisions the workspace; the
spawn endpoint leaves ``workflow_id`` NULL. Step 2.2 will start the
Temporal workflow and update this column with the handle's id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pm_workflow_id"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pms", sa.Column("workflow_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pms", "workflow_id")
