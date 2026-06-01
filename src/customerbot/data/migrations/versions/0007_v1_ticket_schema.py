"""v1 ticket schema (tickets/orgs/articles/links + event logs)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29

Adds the v1 SE-ticketing tables (flow §14) and the four append-only
event-log tables (min-spec §10b). Event-log tables get SQLite triggers
that abort any UPDATE or DELETE — the append-only invariant is enforced
at both the repository level and the DB level.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


_CURRENT = sa.func.current_timestamp()


def upgrade() -> None:
    # --- Tickets ---
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("subtype", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="new"),
        sa.Column("lane", sa.String, nullable=True),
        sa.Column("priority", sa.String, nullable=False, server_default="P3"),
        sa.Column("severity", sa.String, nullable=False, server_default="unsure"),
        sa.Column("feature", sa.String, nullable=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("reporter_user_id", sa.String, nullable=False),
        sa.Column("assigned_user_id", sa.String, nullable=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("original_slack_link", sa.String, nullable=True),
        sa.Column("prod_link", sa.String, nullable=True),
        sa.Column("screenshot_url", sa.String, nullable=True),
        sa.Column("replay_link", sa.String, nullable=True),
        sa.Column("affected_user", sa.String, nullable=True),
        sa.Column("blocking_impact", sa.Text, nullable=True),
        sa.Column("deadline", sa.String, nullable=True),
        sa.Column("card_channel_id", sa.String, nullable=True),
        sa.Column("card_message_ts", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("first_response_at", sa.String, nullable=True),
        sa.Column("resolved_at", sa.String, nullable=True),
        sa.Column("closed_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )
    op.create_index("idx_tickets_status", "tickets", ["status"])
    op.create_index("idx_tickets_priority", "tickets", ["priority"])
    op.create_index("idx_tickets_lane", "tickets", ["lane"])
    op.create_index("idx_tickets_slack_link", "tickets", ["original_slack_link"])
    op.create_index("idx_tickets_feature", "tickets", ["feature"])

    # --- Orgs ---
    op.create_table(
        "orgs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slack_channel_id", sa.String, nullable=True, unique=True),
        sa.Column("acv_tier", sa.String, nullable=True),
        sa.Column("sentiment", sa.String, nullable=True),
        sa.Column("renewal_date", sa.String, nullable=True),
        sa.Column("renewal_status", sa.String, nullable=True),
        sa.Column("csm_user_id", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )

    # --- Ticket ↔ Org (many-to-many) ---
    op.create_table(
        "ticket_orgs",
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), primary_key=True),
        sa.Column("org_id", sa.String, sa.ForeignKey("orgs.id"), primary_key=True),
        sa.Column("added_at", sa.String, nullable=False, server_default=_CURRENT),
    )

    # --- Articles ---
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="suggested"),
        sa.Column("owner_user_id", sa.String, nullable=True),
        sa.Column("url", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.Column("published_at", sa.String, nullable=True),
        sa.Column("updated_at", sa.String, nullable=False, server_default=_CURRENT),
    )

    # --- Ticket ↔ Article (many-to-many) ---
    op.create_table(
        "ticket_articles",
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), primary_key=True),
        sa.Column("article_id", sa.Integer, sa.ForeignKey("articles.id"), primary_key=True),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
    )

    # --- Ticket ↔ Ticket (hotfix-of, dupe-of, article-for, supersedes) ---
    op.create_table(
        "ticket_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("from_ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("to_ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("relation", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False, server_default=_CURRENT),
        sa.UniqueConstraint("from_ticket_id", "to_ticket_id", "relation"),
    )
    op.create_index("idx_ticket_links_from", "ticket_links", ["from_ticket_id"])
    op.create_index("idx_ticket_links_to", "ticket_links", ["to_ticket_id"])

    # --- Event logs (append-only) ---
    op.create_table(
        "event_status_changes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("from_status", sa.String, nullable=True),
        sa.Column("to_status", sa.String, nullable=False),
        sa.Column("by_user_id", sa.String, nullable=True),
        sa.Column("at", sa.String, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("idx_event_status_ticket", "event_status_changes", ["ticket_id"])

    op.create_table(
        "event_prio_changes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("from_priority", sa.String, nullable=True),
        sa.Column("to_priority", sa.String, nullable=False),
        sa.Column("by_user_id", sa.String, nullable=True),
        sa.Column("at", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("idx_event_prio_ticket", "event_prio_changes", ["ticket_id"])

    op.create_table(
        "event_reclassifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("from_type", sa.String, nullable=False),
        sa.Column("to_type", sa.String, nullable=False),
        sa.Column("from_subtype", sa.String, nullable=False),
        sa.Column("to_subtype", sa.String, nullable=False),
        sa.Column("by_user_id", sa.String, nullable=True),
        sa.Column("at", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("next_step", sa.Text, nullable=False, server_default=""),
        sa.Column("owner_user_id", sa.String, nullable=False),
    )
    op.create_index("idx_event_reclass_ticket", "event_reclassifications", ["ticket_id"])

    op.create_table(
        "event_comms_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("sender_user_id", sa.String, nullable=True),
        sa.Column("message_link", sa.String, nullable=True),
        sa.Column("at", sa.String, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("idx_event_comms_ticket", "event_comms_log", ["ticket_id"])

    # --- Append-only triggers (SQLite) ---
    for table in (
        "event_status_changes",
        "event_prio_changes",
        "event_reclassifications",
        "event_comms_log",
    ):
        op.execute(
            f"""
            CREATE TRIGGER prevent_{table}_update
            BEFORE UPDATE ON {table}
            BEGIN
              SELECT RAISE(ABORT, '{table} is append-only; UPDATE forbidden');
            END;
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER prevent_{table}_delete
            BEFORE DELETE ON {table}
            BEGIN
              SELECT RAISE(ABORT, '{table} is append-only; DELETE forbidden');
            END;
            """
        )


def downgrade() -> None:
    for table in (
        "event_status_changes",
        "event_prio_changes",
        "event_reclassifications",
        "event_comms_log",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS prevent_{table}_update")
        op.execute(f"DROP TRIGGER IF EXISTS prevent_{table}_delete")

    op.drop_index("idx_event_comms_ticket", table_name="event_comms_log")
    op.drop_table("event_comms_log")
    op.drop_index("idx_event_reclass_ticket", table_name="event_reclassifications")
    op.drop_table("event_reclassifications")
    op.drop_index("idx_event_prio_ticket", table_name="event_prio_changes")
    op.drop_table("event_prio_changes")
    op.drop_index("idx_event_status_ticket", table_name="event_status_changes")
    op.drop_table("event_status_changes")

    op.drop_index("idx_ticket_links_to", table_name="ticket_links")
    op.drop_index("idx_ticket_links_from", table_name="ticket_links")
    op.drop_table("ticket_links")
    op.drop_table("ticket_articles")
    op.drop_table("articles")
    op.drop_table("ticket_orgs")
    op.drop_table("orgs")

    op.drop_index("idx_tickets_feature", table_name="tickets")
    op.drop_index("idx_tickets_slack_link", table_name="tickets")
    op.drop_index("idx_tickets_lane", table_name="tickets")
    op.drop_index("idx_tickets_priority", table_name="tickets")
    op.drop_index("idx_tickets_status", table_name="tickets")
    op.drop_table("tickets")
