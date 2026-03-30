"""add scope_configs table

Revision ID: a1b2c3d4e5f6
Revises: 3e26952de583
Create Date: 2026-03-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "3e26952de583"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "scope_configs",
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("emoji_config", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.String(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.String(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scope_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("scope_configs")
