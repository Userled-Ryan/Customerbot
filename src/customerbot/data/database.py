from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Index, String, Text, UniqueConstraint, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TrackedConversationRow(Base):
    __tablename__ = "tracked_conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_ts: Mapped[str] = mapped_column(String, nullable=False)
    channel_name: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    category: Mapped[str] = mapped_column(String, nullable=False, server_default="other")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    context: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    last_ryan_reply_at: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[str] = mapped_column(String, nullable=False)
    reminder_sent_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("channel_id", "thread_ts"),
        Index("idx_conversations_status", "status"),
    )


class TrackedKeywordRow(Base):
    __tablename__ = "tracked_keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


class ChannelCursorRow(Base):
    __tablename__ = "channel_cursors"

    integration_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_ts: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


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
