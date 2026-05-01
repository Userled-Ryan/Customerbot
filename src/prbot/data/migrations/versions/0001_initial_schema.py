"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_conversations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.String, nullable=False),
        sa.Column("thread_ts", sa.String, nullable=False),
        sa.Column("channel_name", sa.String, nullable=False, server_default=""),
        sa.Column("category", sa.String, nullable=False, server_default="other"),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column("last_ryan_reply_at", sa.String, nullable=True),
        sa.Column("opened_at", sa.String, nullable=False),
        sa.Column("reminder_sent_at", sa.String, nullable=True),
        sa.Column("created_at", sa.String, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.String, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("channel_id", "thread_ts"),
    )
    op.create_index("idx_conversations_status", "tracked_conversations", ["status"])

    op.create_table(
        "channel_cursors",
        sa.Column("integration_id", sa.String, primary_key=True),
        sa.Column("channel_id", sa.String, primary_key=True),
        sa.Column("last_seen_ts", sa.String, nullable=False),
        sa.Column("created_at", sa.String, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.String, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("tracked_conversations")
    op.drop_table("channel_cursors")
