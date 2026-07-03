"""drop pending_reclassify_sends

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-03

Reclassification no longer stages a draft for the SE to review and send.
On submit, the bot immediately notifies the internal stakeholders, so the
`pending_reclassify_sends` staging table (and its Send/Cancel flow) is gone.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_CURRENT = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.drop_index("idx_pending_reclass_expires_at", table_name="pending_reclassify_sends")
    op.drop_table("pending_reclassify_sends")


def downgrade() -> None:
    op.create_table(
        "pending_reclassify_sends",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column(
            "reclassification_event_id",
            sa.Integer,
            sa.ForeignKey("event_reclassifications.id"),
            nullable=False,
        ),
        sa.Column("recipients_json", sa.Text, nullable=False),
        sa.Column("draft_text", sa.Text, nullable=False),
        sa.Column("dm_channel_id", sa.String, nullable=False),
        sa.Column("dm_message_ts", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("expires_at", sa.String, nullable=False),
    )
    op.create_index("idx_pending_reclass_expires_at", "pending_reclassify_sends", ["expires_at"])
