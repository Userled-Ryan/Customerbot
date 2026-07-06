"""add ticket_support_threads table

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-06

A ticket can be raised from — and later linked to — several separate
#userled-support threads (the same issue reported by more than one person, or
merged in via dedupe). We record every attached support thread so that on
resolve the bot can post the "resolved" reply and swap the 🎫→✅ reaction on
*all* of them, not just the single `original_slack_link`.

`UNIQUE(channel_id, thread_ts)` means a thread belongs to exactly one ticket —
re-linking a thread that's already attached elsewhere reassigns it (the "move").
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_support_threads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("channel_id", sa.String, nullable=False),
        sa.Column("thread_ts", sa.String, nullable=False),
        sa.Column("linked_by", sa.String, nullable=True),
        sa.Column(
            "linked_at", sa.String, nullable=False, server_default=sa.func.current_timestamp()
        ),
        sa.UniqueConstraint("channel_id", "thread_ts"),
    )
    op.create_index("idx_ticket_support_threads_ticket", "ticket_support_threads", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("idx_ticket_support_threads_ticket", table_name="ticket_support_threads")
    op.drop_table("ticket_support_threads")
