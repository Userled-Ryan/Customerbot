"""add tracked keywords

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_keywords",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("word", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.String, server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("tracked_keywords")
