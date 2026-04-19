import pytest

from prbot.application.commands import (
    CommandDispatcher,
    EmojiDomain,
    ExclusionsDomain,
    SelfReviewsDomain,
    ShowConfigCommand,
    build_default_dispatcher,
)
from prbot.application.exclusions.manage_self_reviews import ManageSelfReviews
from prbot.application.exclusions.manage_user_exclusions import ManageUserExclusions
from prbot.domain.emoji.value_objects import EmojiConfig
from tests.conftest import (
    FakeEmojiConfigResolver,
    FakeScopeSettingsRepo,
    FakeUserExclusionRepo,
)

CHANNEL = "slack/T1/C1"
WORKSPACE = "slack/T1"
GLOBAL_ = "slack"
SCOPE_KEYS = [CHANNEL, WORKSPACE, GLOBAL_]


@pytest.fixture
def exclusion_repo() -> FakeUserExclusionRepo:
    return FakeUserExclusionRepo()


@pytest.fixture
def settings_repo() -> FakeScopeSettingsRepo:
    return FakeScopeSettingsRepo()


@pytest.fixture
def manage_exclusions(exclusion_repo: FakeUserExclusionRepo) -> ManageUserExclusions:
    return ManageUserExclusions(exclusion_repo=exclusion_repo)


@pytest.fixture
def manage_self_reviews(settings_repo: FakeScopeSettingsRepo) -> ManageSelfReviews:
    return ManageSelfReviews(settings=settings_repo)


@pytest.fixture
def resolver() -> FakeEmojiConfigResolver:
    return FakeEmojiConfigResolver(EmojiConfig())


@pytest.fixture
def dispatcher(
    manage_exclusions: ManageUserExclusions,
    manage_self_reviews: ManageSelfReviews,
    resolver: FakeEmojiConfigResolver,
) -> CommandDispatcher:
    return build_default_dispatcher(manage_exclusions, manage_self_reviews, resolver)


class TestTopLevelDispatcher:
    async def test_unknown_command_returns_help(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("bogus", [], SCOPE_KEYS)
        assert "prbot commands" in result
        assert "config" in result

    async def test_bare_prbot_routes_to_help(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("help", [], SCOPE_KEYS)
        assert "prbot commands" in result

    async def test_config_bare_returns_summary(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", [], SCOPE_KEYS)
        assert "*Scope:*" in result
        # Emoji summary is always present
        assert "*Emoji config:*" in result

    async def test_config_unknown_domain_shows_help(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", ["nonsense"], SCOPE_KEYS)
        assert "Unknown config domain" in result
        assert "exclusions" in result and "self-reviews" in result


class TestExclusionsDomain:
    async def test_add_then_list(
        self,
        dispatcher: CommandDispatcher,
    ) -> None:
        add = await dispatcher.dispatch(
            "config", ["exclusions", "add", "Cursor", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `Cursor`" in add
        assert WORKSPACE in add

        listing = await dispatcher.dispatch("config", ["exclusions", "list"], SCOPE_KEYS)
        assert "`Cursor`" in listing
        assert "Workspace" in listing

    async def test_remove_idempotent(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", ["exclusions", "remove", "Ghost"], SCOPE_KEYS)
        assert "is not excluded" in result

    async def test_list_with_workspace_scope_isolates(self, dispatcher: CommandDispatcher) -> None:
        await dispatcher.dispatch("config", ["exclusions", "add", "Channel-user"], SCOPE_KEYS)
        await dispatcher.dispatch(
            "config", ["exclusions", "add", "Workspace-user", "workspace"], SCOPE_KEYS
        )

        result = await dispatcher.dispatch(
            "config", ["exclusions", "list", "workspace"], SCOPE_KEYS
        )
        assert "`Workspace-user`" in result
        assert "Channel-user" not in result

    async def test_bare_domain_returns_help(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", ["exclusions"], SCOPE_KEYS)
        assert "*Exclusions*" in result
        assert "add" in result and "remove" in result and "list" in result

    async def test_unknown_action_shows_domain_help(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", ["exclusions", "bonk"], SCOPE_KEYS)
        assert "Unknown action" in result
        assert "*Exclusions*" in result


class TestTopLevelDomains:
    """Domains are registered as top-level Commands — /prbot exclusions add ...
    works the same as /prbot config exclusions add ... (backward compat).
    """

    async def test_exclusions_add_at_top_level(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("exclusions", ["add", "Cursor", "workspace"], SCOPE_KEYS)
        assert "Excluded `Cursor`" in result

    async def test_self_reviews_mute_at_top_level(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("self-reviews", ["mute", "workspace"], SCOPE_KEYS)
        assert "Muted self-reviews" in result

    async def test_emoji_status_at_top_level(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("emoji", ["status"], SCOPE_KEYS)
        assert "Emoji config" in result

    async def test_config_legacy_path_still_works(self, dispatcher: CommandDispatcher) -> None:
        # The old /prbot config exclusions add ... path must keep working.
        result = await dispatcher.dispatch(
            "config", ["exclusions", "add", "Cursor", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `Cursor`" in result

    async def test_help_text_lists_all_top_level_commands(
        self, dispatcher: CommandDispatcher
    ) -> None:
        result = await dispatcher.dispatch("bogus", [], SCOPE_KEYS)
        assert "config" in result
        assert "exclusions" in result
        assert "self-reviews" in result
        assert "emoji" in result


class TestSelfReviewsDomain:
    async def test_mute_then_status(self, dispatcher: CommandDispatcher) -> None:
        muted = await dispatcher.dispatch(
            "config", ["self-reviews", "mute", "workspace"], SCOPE_KEYS
        )
        assert "Muted self-reviews" in muted
        assert WORKSPACE in muted

        status = await dispatcher.dispatch("config", ["self-reviews", "status"], SCOPE_KEYS)
        assert "muted" in status.lower()
        assert WORKSPACE in status

    async def test_unmute_not_set_is_idempotent(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("config", ["self-reviews", "unmute"], SCOPE_KEYS)
        assert "were not muted" in result

    async def test_status_default_shows_inherited(self, dispatcher: CommandDispatcher) -> None:
        await dispatcher.dispatch("config", ["self-reviews", "mute", "workspace"], SCOPE_KEYS)
        # Status from channel scope should surface the workspace mute
        result = await dispatcher.dispatch("config", ["self-reviews", "status"], SCOPE_KEYS)
        assert "Workspace" in result or "workspace" in result
        assert WORKSPACE in result

    async def test_summary_shows_mute(
        self,
        manage_exclusions: ManageUserExclusions,
        manage_self_reviews: ManageSelfReviews,
        resolver: FakeEmojiConfigResolver,
    ) -> None:
        await manage_self_reviews.mute(WORKSPACE)
        config_cmd = ShowConfigCommand(
            [
                ExclusionsDomain(manage_exclusions),
                SelfReviewsDomain(manage_self_reviews),
                EmojiDomain(resolver),
            ]
        )
        result = await config_cmd.execute([], SCOPE_KEYS)
        assert "Self-reviews" in result
        assert "muted" in result
