"""add urgent flag to tickets

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22

Intake now offers an "Urgent" checkbox in place of near-term deadlines (which
the SE team kept missing). An urgent ticket carries no deadline, is forced to
P1, is assigned to the configured SE, and mirrors into Linear's "Urgent"
section while still awaiting first action. Backfilled to 0: existing rows are
not urgent.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("urgent", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("tickets", "urgent")
