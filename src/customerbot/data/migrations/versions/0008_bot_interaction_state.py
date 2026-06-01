"""bot-interaction state tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-29

Ephemeral / cache state the bot uses to operate the v1 flow:
draft modal sessions (anti-phantom 30-min rule §3a), channel→org
cache (ambiguity #1), SLA DM throttling (§8b), three "pending
SE click" tables for dedupe / prio-override / reclassify-send,
and a singleton row for the monthly prio-matrix review reminder
(decision #4).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


_CURRENT = sa.func.current_timestamp()


def upgrade() -> None:
    op.create_table(
        "draft_form_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slack_view_id", sa.String, nullable=False, unique=True),
        sa.Column("modal_kind", sa.String, nullable=False),
        sa.Column("invoker_user_id", sa.String, nullable=False),
        sa.Column("invoker_channel_id", sa.String, nullable=True),
        sa.Column("invoker_thread_ts", sa.String, nullable=True),
        sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("expires_at", sa.String, nullable=False),
    )
    op.create_index("idx_draft_form_sessions_expires_at", "draft_form_sessions", ["expires_at"])

    op.create_table(
        "channel_org_cache",
        sa.Column("slack_channel_id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, sa.ForeignKey("orgs.id"), nullable=True),
        sa.Column("last_synced_at", sa.String, nullable=False),
    )

    op.create_table(
        "sla_dm_state",
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), primary_key=True),
        sa.Column("stage", sa.String, primary_key=True),
        sa.Column("last_state", sa.String, nullable=False),
        sa.Column("last_dm_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )

    op.create_table(
        "pending_dedupe_choices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("candidate_ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("invoker_user_id", sa.String, nullable=False),
        sa.Column("dm_channel_id", sa.String, nullable=False),
        sa.Column("dm_message_ts", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("expires_at", sa.String, nullable=False),
    )
    op.create_index("idx_pending_dedupe_expires_at", "pending_dedupe_choices", ["expires_at"])

    op.create_table(
        "pending_prio_overrides",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("suggested_priority", sa.String, nullable=False),
        sa.Column("dm_channel_id", sa.String, nullable=False),
        sa.Column("dm_message_ts", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("expires_at", sa.String, nullable=False),
    )
    op.create_index("idx_pending_prio_expires_at", "pending_prio_overrides", ["expires_at"])

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

    op.create_table(
        "prio_matrix_review_state",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("last_ack_at", sa.String, nullable=True),
        sa.Column("last_snooze_until", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )


def downgrade() -> None:
    op.drop_table("prio_matrix_review_state")
    op.drop_index("idx_pending_reclass_expires_at", table_name="pending_reclassify_sends")
    op.drop_table("pending_reclassify_sends")
    op.drop_index("idx_pending_prio_expires_at", table_name="pending_prio_overrides")
    op.drop_table("pending_prio_overrides")
    op.drop_index("idx_pending_dedupe_expires_at", table_name="pending_dedupe_choices")
    op.drop_table("pending_dedupe_choices")
    op.drop_table("sla_dm_state")
    op.drop_table("channel_org_cache")
    op.drop_index("idx_draft_form_sessions_expires_at", table_name="draft_form_sessions")
    op.drop_table("draft_form_sessions")
