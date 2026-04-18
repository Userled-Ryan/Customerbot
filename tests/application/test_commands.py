import pytest

from prbot.application.commands import ListExclusionsCommand, ShowConfigCommand
from prbot.application.manage_scope_config import ManageUserExclusions
from prbot.domain.value_objects import EmojiConfig
from tests.conftest import FakeEmojiConfigResolver, FakeUserExclusionRepo

CHANNEL = "slack/T1/C1"
WORKSPACE = "slack/T1"
GLOBAL_ = "slack"


@pytest.fixture
def repo() -> FakeUserExclusionRepo:
    return FakeUserExclusionRepo()


@pytest.fixture
def manage(repo: FakeUserExclusionRepo) -> ManageUserExclusions:
    return ManageUserExclusions(exclusion_repo=repo)


@pytest.fixture
def resolver() -> FakeEmojiConfigResolver:
    return FakeEmojiConfigResolver(EmojiConfig())


@pytest.fixture
def scope_keys() -> list[str]:
    return [CHANNEL, WORKSPACE, GLOBAL_]


class TestShowConfigCommand:
    async def test_no_arg_surfaces_workspace_level_exclusion_at_channel_scope(
        self,
        repo: FakeUserExclusionRepo,
        manage: ManageUserExclusions,
        resolver: FakeEmojiConfigResolver,
        scope_keys: list[str],
    ) -> None:
        await repo.add(WORKSPACE, "Cursor")

        cmd = ShowConfigCommand(manage, resolver)
        result = await cmd.execute([], scope_keys)

        assert "*Excluded users:*" in result
        assert "*Workspace*" in result
        assert "`Cursor`" in result
        assert "*Excluded users:* none" not in result

    async def test_no_arg_groups_exclusions_by_scope(
        self,
        repo: FakeUserExclusionRepo,
        manage: ManageUserExclusions,
        resolver: FakeEmojiConfigResolver,
        scope_keys: list[str],
    ) -> None:
        await repo.add(CHANNEL, "channel-user")
        await repo.add(WORKSPACE, "workspace-user")

        cmd = ShowConfigCommand(manage, resolver)
        result = await cmd.execute([], scope_keys)

        assert "*Channel*" in result and "`channel-user`" in result
        assert "*Workspace*" in result and "`workspace-user`" in result

    async def test_explicit_workspace_shows_only_workspace_level(
        self,
        repo: FakeUserExclusionRepo,
        manage: ManageUserExclusions,
        resolver: FakeEmojiConfigResolver,
        scope_keys: list[str],
    ) -> None:
        await repo.add(CHANNEL, "channel-user")
        await repo.add(WORKSPACE, "workspace-user")

        cmd = ShowConfigCommand(manage, resolver)
        result = await cmd.execute(["workspace"], scope_keys)

        assert "`workspace-user`" in result
        assert "channel-user" not in result

    async def test_no_exclusions_says_none(
        self,
        manage: ManageUserExclusions,
        resolver: FakeEmojiConfigResolver,
        scope_keys: list[str],
    ) -> None:
        cmd = ShowConfigCommand(manage, resolver)
        result = await cmd.execute([], scope_keys)
        assert "*Excluded users:* none" in result

    async def test_unknown_scope_returns_error(
        self,
        manage: ManageUserExclusions,
        resolver: FakeEmojiConfigResolver,
        scope_keys: list[str],
    ) -> None:
        cmd = ShowConfigCommand(manage, resolver)
        result = await cmd.execute(["bogus"], scope_keys)
        assert "Unknown scope" in result


class TestListExclusionsCommand:
    async def test_default_surfaces_workspace_inherited(
        self,
        repo: FakeUserExclusionRepo,
        manage: ManageUserExclusions,
        scope_keys: list[str],
    ) -> None:
        await repo.add(WORKSPACE, "Cursor")

        cmd = ListExclusionsCommand(manage)
        result = await cmd.execute([], scope_keys)

        assert "*Workspace*" in result
        assert "`Cursor`" in result
