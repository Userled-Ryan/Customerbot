"""add dev_owner_user_id to tickets

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-30

Moving a ticket to the Dev Action lane now records *which* dev picked it up —
the current member of the `@support` user-group — and puts the Linear issue in
their name. Kept separate from `se_owner_user_id` so the card can show both and
the SE round-robin keeps its own notion of ownership. Nullable: existing rows
have no dev recorded, which is exactly right for anything still on the SE lane.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("dev_owner_user_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "dev_owner_user_id")
