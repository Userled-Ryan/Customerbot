"""add user_settings table and reminder_interval_hours to conversations

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracked_conversations",
        sa.Column("reminder_interval_hours", sa.Integer, nullable=True),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.String, primary_key=True),
        sa.Column("timezone", sa.String, nullable=False, server_default="UTC"),
        sa.Column("default_reminder_hours", sa.Integer, nullable=False, server_default="24"),
        sa.Column("daily_digest_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_morning_digest_date", sa.String, nullable=True),
        sa.Column("last_evening_digest_date", sa.String, nullable=True),
        sa.Column("created_at", sa.String, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.String, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_column("tracked_conversations", "reminder_interval_hours")
