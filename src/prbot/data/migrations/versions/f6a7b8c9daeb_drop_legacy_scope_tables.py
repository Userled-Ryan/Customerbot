"""drop legacy scope_configs and user_exclusions tables

Revision ID: f6a7b8c9daeb
Revises: e5f6a7b8c9da
Create Date: 2026-04-18 00:00:00.000000

All reads and writes now go through ``scope_settings`` (introduced and
backfilled in ``e5f6a7b8c9da``). The previous migration left the legacy
tables in place as a rollback escape hatch; this one retires them.

Downgrade recreates the empty tables so the earlier migration's
backfill SQL can re-run cleanly, but the original row data is gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9daeb"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("user_exclusions")
    op.drop_table("scope_configs")


def downgrade() -> None:
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
    op.create_table(
        "user_exclusions",
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("scope_key", "username"),
    )
