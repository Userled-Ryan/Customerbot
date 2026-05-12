"""mark created_at/updated_at columns NOT NULL

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_CURRENT_TIMESTAMP = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    with op.batch_alter_table("channel_cursors") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("tracked_conversations") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("tracked_keywords") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=False,
            existing_server_default=_CURRENT_TIMESTAMP,
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("tracked_keywords") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("tracked_conversations") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )

    with op.batch_alter_table("channel_cursors") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            nullable=True,
            existing_server_default=_CURRENT_TIMESTAMP,
        )
