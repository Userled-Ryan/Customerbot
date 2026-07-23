from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# -----------------------------------------------------------------------------
# Legacy tables (prbot-era). The code that read/wrote them (the `/csbot`
# command + its repositories) was removed; these mappings are kept only so the
# tables and any historical rows survive and the migration check stays in sync.
# -----------------------------------------------------------------------------


class TrackedConversationRow(Base):
    __tablename__ = "tracked_conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_number: Mapped[int | None] = mapped_column(nullable=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_ts: Mapped[str] = mapped_column(String, nullable=False)
    channel_name: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    category: Mapped[str] = mapped_column(String, nullable=False, server_default="other")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    context: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    last_ryan_reply_at: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[str] = mapped_column(String, nullable=False)
    reminder_sent_at: Mapped[str | None] = mapped_column(String, nullable=True)
    reminder_interval_hours: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("channel_id", "thread_ts"),
        Index("idx_conversations_status", "status"),
    )


class UserSettingsRow(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, server_default="UTC")
    default_reminder_hours: Mapped[int] = mapped_column(nullable=False, server_default="24")
    daily_digest_enabled: Mapped[int] = mapped_column(nullable=False, server_default="1")
    last_morning_digest_date: Mapped[str | None] = mapped_column(String, nullable=True)
    last_evening_digest_date: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


class TrackedKeywordRow(Base):
    __tablename__ = "tracked_keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


class ChannelCursorRow(Base):
    __tablename__ = "channel_cursors"

    integration_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_ts: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


# -----------------------------------------------------------------------------
# v1 ticket data (flow §14)
# -----------------------------------------------------------------------------


class TicketRow(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    subtype: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="new")
    lane: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default="P3")
    severity: Mapped[str] = mapped_column(String, nullable=False, server_default="unsure")
    feature: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reporter_user_id: Mapped[str] = mapped_column(String, nullable=False)
    assigned_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # SE owner (migration 0016) — set to the configured SE on creation, editable
    # from the card's SE-owner dropdown, mirrored to Linear as the assignee.
    se_owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    original_slack_link: Mapped[str | None] = mapped_column(String, nullable=True)
    prod_link: Mapped[str | None] = mapped_column(String, nullable=True)
    screenshot_url: Mapped[str | None] = mapped_column(String, nullable=True)
    replay_link: Mapped[str | None] = mapped_column(String, nullable=True)
    # Campaign link (migration 0017) — kept separate from prod_link so it
    # doesn't participate in exact-match dedupe.
    campaign_url: Mapped[str | None] = mapped_column(String, nullable=True)
    affected_user: Mapped[str | None] = mapped_column(String, nullable=True)
    blocking_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[str | None] = mapped_column(String, nullable=True)
    # Urgent flag (migration 0018) — set from the intake "Urgent" checkbox. An
    # urgent ticket has no deadline, is forced to P1, and mirrors into Linear's
    # "Urgent" section while still NEW.
    urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    # Resolution reporting (migration 0013) — set when the SE marks a ticket
    # Resolved via the resolve modal. `resolution_pr_link` only set for a
    # code-change resolution.
    resolution_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_pr_link: Mapped[str | None] = mapped_column(String, nullable=True)
    card_channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    card_message_ts: Mapped[str | None] = mapped_column(String, nullable=True)
    # SE-set "waiting on a reply" flag (migration 0012). Drives the card badge
    # and the daily 5pm reply-needed digest.
    reply_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    # Linear mirror (v1.5, migration 0011) — set once when the ticket is first
    # mirrored; the inbound webhook maps Linear → ticket via linear_issue_id.
    linear_issue_id: Mapped[str | None] = mapped_column(String, nullable=True)
    linear_issue_identifier: Mapped[str | None] = mapped_column(String, nullable=True)
    linear_issue_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    first_response_at: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_tickets_status", "status"),
        Index("idx_tickets_priority", "priority"),
        Index("idx_tickets_lane", "lane"),
        Index("idx_tickets_slack_link", "original_slack_link"),
        Index("idx_tickets_feature", "feature"),
        Index("idx_tickets_linear_issue_id", "linear_issue_id"),
    )


class OrgRow(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slack_channel_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    teams_channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    acv_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String, nullable=True)
    renewal_date: Mapped[str | None] = mapped_column(String, nullable=True)
    renewal_status: Mapped[str | None] = mapped_column(String, nullable=True)
    csm_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class TicketOrgRow(Base):
    __tablename__ = "ticket_orgs"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    added_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class ArticleRow(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="suggested")
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    published_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class TicketArticleRow(Base):
    __tablename__ = "ticket_articles"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class TicketLinkRow(Base):
    __tablename__ = "ticket_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    to_ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("from_ticket_id", "to_ticket_id", "relation"),
        Index("idx_ticket_links_from", "from_ticket_id"),
        Index("idx_ticket_links_to", "to_ticket_id"),
    )


class TicketSupportThreadRow(Base):
    """Every #userled-support thread attached to a ticket (migration 0015).

    A ticket may be raised from — and later linked to — several support
    threads. On resolve the bot fans the "resolved" reply + 🎫→✅ reaction out
    across all of them. `UNIQUE(channel_id, thread_ts)` keeps a thread on
    exactly one ticket, so re-linking reassigns (the "move").
    """

    __tablename__ = "ticket_support_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_ts: Mapped[str] = mapped_column(String, nullable=False)
    linked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("channel_id", "thread_ts"),
        Index("idx_ticket_support_threads_ticket", "ticket_id"),
    )


# -----------------------------------------------------------------------------
# Event-log tables (append-only — DB triggers in migration 0007 block UPDATE/DELETE)
# -----------------------------------------------------------------------------


class EventStatusChangeRow(Base):
    __tablename__ = "event_status_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (Index("idx_event_status_ticket", "ticket_id"),)


class EventPrioChangeRow(Base):
    __tablename__ = "event_prio_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    from_priority: Mapped[str | None] = mapped_column(String, nullable=True)
    to_priority: Mapped[str] = mapped_column(String, nullable=False)
    by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (Index("idx_event_prio_ticket", "ticket_id"),)


class EventReclassificationRow(Base):
    __tablename__ = "event_reclassifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    from_type: Mapped[str] = mapped_column(String, nullable=False)
    to_type: Mapped[str] = mapped_column(String, nullable=False)
    from_subtype: Mapped[str] = mapped_column(String, nullable=False)
    to_subtype: Mapped[str] = mapped_column(String, nullable=False)
    by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    next_step: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    owner_user_id: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_event_reclass_ticket", "ticket_id"),)


class EventCommsLogRow(Base):
    __tablename__ = "event_comms_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    sender_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message_link: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    __table_args__ = (Index("idx_event_comms_ticket", "ticket_id"),)


# -----------------------------------------------------------------------------
# Bot-interaction state (ephemeral; not authoritative ticket data)
# -----------------------------------------------------------------------------


class DraftFormSessionRow(Base):
    __tablename__ = "draft_form_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slack_view_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    modal_kind: Mapped[str] = mapped_column(String, nullable=False)
    invoker_user_id: Mapped[str] = mapped_column(String, nullable=False)
    invoker_channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    invoker_thread_ts: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_draft_form_sessions_expires_at", "expires_at"),)


class ChannelOrgCacheRow(Base):
    __tablename__ = "channel_org_cache"

    slack_channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("orgs.id"), nullable=True)
    last_synced_at: Mapped[str] = mapped_column(String, nullable=False)


class SLADMStateRow(Base):
    __tablename__ = "sla_dm_state"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String, primary_key=True)
    last_state: Mapped[str] = mapped_column(String, nullable=False)
    last_dm_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class PendingDedupeChoiceRow(Base):
    __tablename__ = "pending_dedupe_choices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    candidate_ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    invoker_user_id: Mapped[str] = mapped_column(String, nullable=False)
    dm_channel_id: Mapped[str] = mapped_column(String, nullable=False)
    dm_message_ts: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_pending_dedupe_expires_at", "expires_at"),)


class PendingPrioOverrideRow(Base):
    __tablename__ = "pending_prio_overrides"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    suggested_priority: Mapped[str] = mapped_column(String, nullable=False)
    dm_channel_id: Mapped[str] = mapped_column(String, nullable=False)
    dm_message_ts: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("idx_pending_prio_expires_at", "expires_at"),)


class PrioMatrixReviewStateRow(Base):
    __tablename__ = "prio_matrix_review_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    last_ack_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_snooze_until: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


class WeeklyDigestStateRow(Base):
    __tablename__ = "weekly_digest_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    last_fired_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, server_default=func.current_timestamp()
    )


# -----------------------------------------------------------------------------
# Engine / sessions / migration runner
# -----------------------------------------------------------------------------


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, echo=False)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def database_url_from_path(db_path: str) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _sync_url(database_url: str) -> str:
    return database_url.replace("+aiosqlite", "")


def run_migrations(database_url: str) -> None:
    migrations_dir = str(Path(__file__).parent / "migrations")
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", migrations_dir)
    alembic_cfg.set_main_option("sqlalchemy.url", _sync_url(database_url))
    command.upgrade(alembic_cfg, "head")
