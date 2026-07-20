"""add campaign_url to tickets

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-20

Intake now asks "Is part of campaign? = Yes/No"; when Yes, the SE supplies the
campaign's URL. It's stored in a dedicated column (deliberately separate from
`prod_link`, which drives exact-match dedupe) and surfaced on the ticket card
and the mirrored Linear issue. Nullable: existing rows simply have no campaign
recorded.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("campaign_url", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "campaign_url")
