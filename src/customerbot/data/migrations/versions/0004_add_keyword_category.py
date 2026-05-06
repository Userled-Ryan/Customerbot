"""add category column to tracked_keywords

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_keywords", sa.Column("category", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_keywords", "category")
