"""add resolution_type / resolution_pr_link to tickets

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-26

When the SE marks a ticket Resolved (plan Part 2) the resolve modal captures
*how* it was resolved — `no-code-change` or `code-change` (+ optional PR link)
— for reporting. Both columns are nullable: existing rows (and any ticket not
resolved through the modal) simply have no resolution recorded.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("resolution_type", sa.String, nullable=True))
    op.add_column("tickets", sa.Column("resolution_pr_link", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "resolution_pr_link")
    op.drop_column("tickets", "resolution_type")
