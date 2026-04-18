import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prbot.data.database import Base
from prbot.data.scope_config import ScopeConfigEmojiResolver
from prbot.data.scope_settings import SQLiteScopeSettingsRepository
from prbot.data.user_exclusions import SQLiteUserExclusionRepository
from prbot.domain.emoji.value_objects import EmojiConfig


@pytest.fixture
async def session_factory() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def settings_repo(
    session_factory: async_sessionmaker,
) -> SQLiteScopeSettingsRepository:
    return SQLiteScopeSettingsRepository(session_factory=session_factory)


class TestSQLiteScopeSettingsRepository:
    async def test_get_missing_returns_none(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        assert await settings_repo.get(["slack/T1/C1"], "emoji") is None

    async def test_set_then_get(self, settings_repo: SQLiteScopeSettingsRepository) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "party"})
        assert await settings_repo.get(["slack/T1"], "emoji") == {"merged": "party"}

    async def test_get_walks_most_specific_first(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "ws"})
        await settings_repo.set("slack/T1/C1", "emoji", {"merged": "ch"})

        value = await settings_repo.get(["slack/T1/C1", "slack/T1"], "emoji")
        assert value == {"merged": "ch"}

    async def test_get_falls_through_unset_specific_scope(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "ws"})

        value = await settings_repo.get(["slack/T1/C1", "slack/T1"], "emoji")
        assert value == {"merged": "ws"}

    async def test_set_overwrites_existing(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "old"})
        await settings_repo.set("slack/T1", "emoji", {"merged": "new"})
        assert await settings_repo.get(["slack/T1"], "emoji") == {"merged": "new"}

    async def test_unset_removes_row(self, settings_repo: SQLiteScopeSettingsRepository) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "x"})
        assert await settings_repo.unset("slack/T1", "emoji") is True
        assert await settings_repo.get(["slack/T1"], "emoji") is None

    async def test_unset_missing_returns_false(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        assert await settings_repo.unset("slack/T1", "emoji") is False

    async def test_get_all_at_groups_by_scope(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        await settings_repo.set("slack/T1", "excluded_users", ["alice"])
        await settings_repo.set("slack/T1/C1", "excluded_users", ["bob"])
        await settings_repo.set("slack/T1", "emoji", {"merged": "ws"})

        grouped = await settings_repo.get_all_at(["slack/T1/C1", "slack/T1"], "excluded_users")
        assert grouped == {"slack/T1/C1": ["bob"], "slack/T1": ["alice"]}

    async def test_different_keys_independent(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "x"})
        await settings_repo.set("slack/T1", "excluded_users", ["alice"])

        assert await settings_repo.get(["slack/T1"], "emoji") == {"merged": "x"}
        assert await settings_repo.get(["slack/T1"], "excluded_users") == ["alice"]


class TestSQLiteUserExclusionRepositoryOnSettings:
    @pytest.fixture
    async def repo(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> SQLiteUserExclusionRepository:
        return SQLiteUserExclusionRepository(settings=settings_repo)

    async def test_add_then_is_excluded(self, repo: SQLiteUserExclusionRepository) -> None:
        assert await repo.add("slack/T1/C1", "Cursor") is True
        assert await repo.is_excluded(["slack/T1/C1"], "Cursor") is True

    async def test_add_is_case_insensitive_match(self, repo: SQLiteUserExclusionRepository) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        assert await repo.is_excluded(["slack/T1/C1"], "cursor") is True

    async def test_add_idempotent(self, repo: SQLiteUserExclusionRepository) -> None:
        assert await repo.add("slack/T1/C1", "Cursor") is True
        assert await repo.add("slack/T1/C1", "Cursor") is False

    async def test_remove_existing(self, repo: SQLiteUserExclusionRepository) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        assert await repo.remove("slack/T1/C1", "Cursor") is True
        assert await repo.is_excluded(["slack/T1/C1"], "Cursor") is False

    async def test_remove_last_user_unsets_key(
        self,
        repo: SQLiteUserExclusionRepository,
        settings_repo: SQLiteScopeSettingsRepository,
    ) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        await repo.remove("slack/T1/C1", "Cursor")

        # Ensure the underlying setting is gone, not just an empty array
        assert await settings_repo.get(["slack/T1/C1"], "excluded_users") is None

    async def test_remove_missing_returns_false(self, repo: SQLiteUserExclusionRepository) -> None:
        assert await repo.remove("slack/T1/C1", "Cursor") is False

    async def test_is_excluded_walks_scopes(self, repo: SQLiteUserExclusionRepository) -> None:
        await repo.add("slack/T1", "dependabot[bot]")
        assert await repo.is_excluded(["slack/T1/C1", "slack/T1"], "dependabot[bot]") is True

    async def test_list_excluded_groups_by_scope(self, repo: SQLiteUserExclusionRepository) -> None:
        await repo.add("slack/T1/C1", "Cursor")
        await repo.add("slack/T1/C1", "bot")
        await repo.add("slack/T1", "dependabot[bot]")

        grouped = await repo.list_excluded(["slack/T1/C1", "slack/T1"])

        assert sorted(grouped["slack/T1/C1"]) == ["Cursor", "bot"]
        assert grouped["slack/T1"] == ["dependabot[bot]"]

    async def test_list_excluded_omits_empty_scopes(
        self, repo: SQLiteUserExclusionRepository
    ) -> None:
        await repo.add("slack/T1", "dependabot[bot]")

        grouped = await repo.list_excluded(["slack/T1/C1", "slack/T1"])

        assert "slack/T1/C1" not in grouped


class TestScopeConfigEmojiResolverOnSettings:
    @pytest.fixture
    async def resolver(
        self, settings_repo: SQLiteScopeSettingsRepository
    ) -> ScopeConfigEmojiResolver:
        return ScopeConfigEmojiResolver(settings=settings_repo, default=EmojiConfig())

    async def test_returns_default_when_unset(self, resolver: ScopeConfigEmojiResolver) -> None:
        config = await resolver.resolve(["slack/T1/C1"])
        assert config == EmojiConfig()

    async def test_returns_scoped_override(
        self,
        resolver: ScopeConfigEmojiResolver,
        settings_repo: SQLiteScopeSettingsRepository,
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "party_parrot"})
        config = await resolver.resolve(["slack/T1/C1", "slack/T1"])
        assert config.merged == "party_parrot"

    async def test_prefers_most_specific_scope(
        self,
        resolver: ScopeConfigEmojiResolver,
        settings_repo: SQLiteScopeSettingsRepository,
    ) -> None:
        await settings_repo.set("slack/T1", "emoji", {"merged": "workspace"})
        await settings_repo.set("slack/T1/C1", "emoji", {"merged": "channel"})
        config = await resolver.resolve(["slack/T1/C1", "slack/T1"])
        assert config.merged == "channel"
