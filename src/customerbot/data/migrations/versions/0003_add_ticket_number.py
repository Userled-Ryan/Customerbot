"""add ticket_number column

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_conversations", sa.Column("ticket_number", sa.Integer, nullable=True))

    # Backfill open rows: assign 1..N in ascending id order
    conn = op.get_bind()
    open_rows = conn.execute(
        sa.text("SELECT id FROM tracked_conversations WHERE status = 'open' ORDER BY id")
    ).fetchall()
    for i, (row_id,) in enumerate(open_rows, start=1):
        conn.execute(
            sa.text("UPDATE tracked_conversations SET ticket_number = :n WHERE id = :id"),
            {"n": i, "id": row_id},
        )


def downgrade() -> None:
    op.drop_column("tracked_conversations", "ticket_number")
