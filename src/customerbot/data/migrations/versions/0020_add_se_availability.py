"""add se_availability table

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-29

Sick / holiday mode (`/ooo`). One row per SE currently marked out: new tickets
that would land on them go to `cover_user_id` (or the rest of the round-robin
pool when unset) until `back_on` — the first day they're back — or until they're
marked back. Runtime state, so it lives here rather than in a Fly secret.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "se_availability",
        sa.Column("user_id", sa.String, primary_key=True),
        sa.Column("cover_user_id", sa.String, nullable=True),
        sa.Column("back_on", sa.String, nullable=True),
        sa.Column("set_by_user_id", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("se_availability")
