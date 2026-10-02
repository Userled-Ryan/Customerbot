"""add community_invites table

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-02

Ledger for `scripts/add_customers_to_community_channel.py`, which invites
external customer users from the customer channels into #userled-community.
One row per user the sweep has seen: `invited`, `already_member`, `left` or
`failed`. Every status but `failed` blocks future invites, so anyone who leaves
the community channel is never re-added.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_invites",
        sa.Column("user_id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("team_id", sa.String, nullable=True),
        sa.Column("email", sa.String, nullable=True),
        sa.Column("source_org", sa.String, nullable=True),
        sa.Column("source_channel_id", sa.String, nullable=True),
        sa.Column("detail", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("community_invites")
