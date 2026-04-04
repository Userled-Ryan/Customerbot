from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Index, String, Text, UniqueConstraint, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TrackedPRRow(Base):
    __tablename__ = "tracked_prs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    pr_number: Mapped[int] = mapped_column(nullable=False)
    integration_id: Mapped[str] = mapped_column(String, nullable=False, server_default="slack")
    message_ref: Mapped[str] = mapped_column(String, nullable=False)
    applied_emojis: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    scope_keys: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint("owner", "repo", "pr_number", "integration_id", "message_ref"),
        Index("idx_tracked_prs_lookup", "owner", "repo", "pr_number"),
    )


class ChannelCursorRow(Base):
    __tablename__ = "channel_cursors"

    integration_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_ts: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


class ScopeConfigRow(Base):
    __tablename__ = "scope_configs"

    scope_key: Mapped[str] = mapped_column(String, primary_key=True)
    emoji_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[str] = mapped_column(server_default=func.current_timestamp())


def make_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine from a database URL."""
    return create_async_engine(database_url, echo=False)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


def database_url_from_path(db_path: str) -> str:
    """Convert a file path like 'data/pr_bot.db' to an async SQLite URL."""
    return f"sqlite+aiosqlite:///{db_path}"


def _sync_url(database_url: str) -> str:
    """Convert an async database URL to its sync equivalent for Alembic."""
    return database_url.replace("+aiosqlite", "")


def run_migrations(database_url: str) -> None:
    """Run Alembic migrations synchronously to bring the DB up to head.

    Converts the async URL to sync so Alembic doesn't need asyncio.run(),
    which would fail when called from within an already-running event loop
    (e.g. FastAPI lifespan).
    """
    migrations_dir = str(Path(__file__).parent / "migrations")
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", migrations_dir)
    alembic_cfg.set_main_option("sqlalchemy.url", _sync_url(database_url))
    command.upgrade(alembic_cfg, "head")
