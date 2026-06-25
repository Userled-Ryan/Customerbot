"""friday per-CSM digest singleton state

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-25

Tracks the last time `FridayCSMDigestJob` fired so the 30-min loop doesn't
re-DM every CSM if it ticks twice inside the Friday 12:00 SE-local window.
Singleton — one row, lazily created on first read.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


_CURRENT = sa.func.current_timestamp()


def upgrade() -> None:
    op.create_table(
        "csm_digest_state",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("last_fired_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )


def downgrade() -> None:
    op.drop_table("csm_digest_state")
