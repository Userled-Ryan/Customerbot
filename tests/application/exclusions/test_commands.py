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
    FakeGitHubUserLookup,
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
def github_lookup() -> FakeGitHubUserLookup:
    return FakeGitHubUserLookup()


@pytest.fixture
def manage_exclusions(
    exclusion_repo: FakeUserExclusionRepo,
    github_lookup: FakeGitHubUserLookup,
) -> ManageUserExclusions:
    return ManageUserExclusions(exclusion_repo=exclusion_repo, github_lookup=github_lookup)


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


class TestExclusionsAddValidation:
    async def test_add_real_user_has_no_warning(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("octocat", "user")
        result = await dispatcher.dispatch(
            "exclusions", ["add", "octocat", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `octocat`" in result
        assert "⚠" not in result
        assert "🤖" not in result

    async def test_add_unknown_login_warns(
        self,
        dispatcher: CommandDispatcher,
    ) -> None:
        result = await dispatcher.dispatch(
            "exclusions", ["add", "definitely-not-a-user-xyz", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `definitely-not-a-user-xyz`" in result
        assert "No GitHub account" in result

    async def test_add_bot_without_suffix_warns(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("copilot-pull-request-reviewer", "bot")
        result = await dispatcher.dispatch(
            "exclusions", ["add", "copilot-pull-request-reviewer", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `copilot-pull-request-reviewer`" in result
        assert "GitHub App" in result
        assert "[bot]" in result

    async def test_add_bot_with_suffix_has_no_warning(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("copilot-pull-request-reviewer", "bot")
        result = await dispatcher.dispatch(
            "exclusions",
            ["add", "copilot-pull-request-reviewer[bot]", "workspace"],
            SCOPE_KEYS,
        )
        assert "Excluded `copilot-pull-request-reviewer[bot]`" in result
        assert "⚠" not in result

    async def test_add_organization_warns(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("anthropics", "organization")
        result = await dispatcher.dispatch(
            "exclusions", ["add", "anthropics", "workspace"], SCOPE_KEYS
        )
        assert "organization" in result

    async def test_add_when_lookup_fails_still_stores(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.raise_for("octocat")
        result = await dispatcher.dispatch(
            "exclusions", ["add", "octocat", "workspace"], SCOPE_KEYS
        )
        assert "Excluded `octocat`" in result
        assert "Could not verify" in result


class TestExclusionsCheck:
    async def test_check_empty(self, dispatcher: CommandDispatcher) -> None:
        result = await dispatcher.dispatch("exclusions", ["check"], SCOPE_KEYS)
        assert "No users are excluded" in result

    async def test_check_mixed_entries(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("octocat", "user")
        github_lookup.seed("copilot-pull-request-reviewer", "bot")
        # Add without validation side effects we care about — just populate storage.
        github_lookup.seed("anthropics", "organization")

        await dispatcher.dispatch("exclusions", ["add", "octocat", "workspace"], SCOPE_KEYS)
        await dispatcher.dispatch(
            "exclusions",
            ["add", "copilot-pull-request-reviewer[bot]", "workspace"],
            SCOPE_KEYS,
        )
        await dispatcher.dispatch("exclusions", ["add", "anthropics", "workspace"], SCOPE_KEYS)
        await dispatcher.dispatch("exclusions", ["add", "ghost-user", "workspace"], SCOPE_KEYS)

        result = await dispatcher.dispatch("exclusions", ["check", "workspace"], SCOPE_KEYS)
        assert "✓ user" in result  # octocat
        assert "🤖 bot" in result  # bot, properly suffixed
        assert "organization" in result  # anthropics
        assert "not found on GitHub" in result  # ghost-user

    async def test_check_flags_bot_stored_without_suffix(
        self,
        dispatcher: CommandDispatcher,
        github_lookup: FakeGitHubUserLookup,
    ) -> None:
        github_lookup.seed("cursor", "bot")
        # Add the bot without the [bot] suffix (user mistake). The add command warns,
        # but storage takes the value verbatim; check should keep warning.
        await dispatcher.dispatch("exclusions", ["add", "cursor", "workspace"], SCOPE_KEYS)

        result = await dispatcher.dispatch("exclusions", ["check", "workspace"], SCOPE_KEYS)
        assert "will not match" in result
        assert "cursor[bot]" in result


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
