"""add teams_channel_id to orgs

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-10

Some customers are reached via a Microsoft Teams channel rather than a Slack
channel. v1 only modelled `slack_channel_id`; this adds a parallel nullable
`teams_channel_id` so an org can carry either (or neither, or both) without
forcing a Slack ID on Teams-only customers. Nullable + no backfill — existing
rows keep `teams_channel_id = NULL`.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("teams_channel_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "teams_channel_id")
