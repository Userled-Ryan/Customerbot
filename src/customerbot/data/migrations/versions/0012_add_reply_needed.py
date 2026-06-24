"""add reply_needed flag to tickets

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-24

The SE flags a ticket as "waiting on a reply" from the ticket card. The flag
drives the card badge and the daily 5pm reply-needed digest DM. It's plain
metadata (no event-log row) — SE is the only writer, toggled on/off.

Non-nullable with a `0` default and no backfill: existing rows default to
"no reply needed", which is correct.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("reply_needed", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("tickets", "reply_needed")
