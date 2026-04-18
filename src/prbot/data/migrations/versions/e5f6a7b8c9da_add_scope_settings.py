"""add scope_settings table and backfill from existing config tables

Revision ID: e5f6a7b8c9da
Revises: d4e5f6a7b8c9
Create Date: 2026-04-18 00:00:00.000000

Creates a generic key/value-per-scope settings table. Backfills emoji
configuration from ``scope_configs`` (one row per scope, key='emoji')
and user exclusions from ``user_exclusions`` (one row per scope,
key='excluded_users', value=JSON array of usernames).

The old tables are left in place so this migration can be rolled back
by dropping only the new table; they are removed in a later migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9da"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scope_settings",
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("scope_key", "key"),
        if_not_exists=True,
    )

    op.execute(
        """
        INSERT INTO scope_settings (scope_key, key, value)
        SELECT scope_key, 'emoji', emoji_config
        FROM scope_configs
        WHERE emoji_config IS NOT NULL
        """
    )

    op.execute(
        """
        INSERT INTO scope_settings (scope_key, key, value)
        SELECT scope_key, 'excluded_users', json_group_array(username)
        FROM user_exclusions
        GROUP BY scope_key
        """
    )


def downgrade() -> None:
    op.drop_table("scope_settings")
