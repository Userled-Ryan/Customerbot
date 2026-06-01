"""weekly-digest singleton state

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-01

Tracks the last time `WeeklyDigestJob` (Chunk 13) fired so the loop
doesn't re-send the digest if it ticks twice inside the same 09:00
SE-local Monday window. Singleton — one row, lazily created on first
read.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


_CURRENT = sa.func.current_timestamp()


def upgrade() -> None:
    op.create_table(
        "weekly_digest_state",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("last_fired_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )


def downgrade() -> None:
    op.drop_table("weekly_digest_state")
