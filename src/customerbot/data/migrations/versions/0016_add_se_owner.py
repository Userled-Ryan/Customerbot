"""add se_owner_user_id to tickets

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-13

Every ticket now carries an *SE owner* — the SE responsible for it. It's set to
the configured default SE on creation (never exposed to the logger) and
reassigned from the ticket card's SE-owner dropdown; it's also mirrored to the
Linear issue as the assignee. Nullable: existing rows simply have no owner
recorded until next touched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("se_owner_user_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "se_owner_user_id")
