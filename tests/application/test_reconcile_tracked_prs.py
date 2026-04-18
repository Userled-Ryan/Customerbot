import pytest

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.application.reconcile_tracked_prs import ReconcileTrackedPRs
from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRInfo, PRUrl
from tests.conftest import (
    FakeEmojiConfigResolver,
    FakePRRepository,
    FakePRSource,
    FakeReactions,
    FakeScopeSettingsRepo,
    FakeUserExclusionRepo,
)


def _pr_url(number: int = 1) -> PRUrl:
    return PRUrl(owner="o", repo="r", number=number)


def _msg_ref(channel: str = "C123", ts: str = "1234.5678") -> MessageRef:
    return MessageRef(integration_id="slack", ref=f"{channel}:{ts}")


MERGED_INFO = PRInfo(state="closed", merged=True, reviews=())
OPEN_INFO = PRInfo(state="open", merged=False, reviews=())


class _FailingPRSource(FakePRSource):
    """A source that raises for specific PR numbers."""

    def __init__(self, pr_info: PRInfo, failing_numbers: set[int]) -> None:
        super().__init__(pr_info)
        self._failing_numbers = failing_numbers

    async def fetch_pr_info(self, pr_url: PRUrl) -> PRInfo:
        if pr_url.number in self._failing_numbers:
            raise RuntimeError(f"Simulated failure for PR #{pr_url.number}")
        return await super().fetch_pr_info(pr_url)


def _make_use_case(
    source: FakePRSource,
    reactions: FakeReactions,
    repo: FakePRRepository,
    resolver: FakeEmojiConfigResolver,
    exclusions: FakeUserExclusionRepo | None = None,
) -> ReconcileTrackedPRs:
    webhook = HandleGitHubWebhook(
        source,
        reactions,
        repo,
        resolver,
        exclusions or FakeUserExclusionRepo(),
        FakeScopeSettingsRepo(),
    )
    return ReconcileTrackedPRs(pr_repository=repo, handle_webhook=webhook)


class TestReconcileTrackedPRs:
    @pytest.fixture
    def reactions(self) -> FakeReactions:
        return FakeReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    @pytest.fixture
    def resolver(self) -> FakeEmojiConfigResolver:
        return FakeEmojiConfigResolver()

    async def test_no_tracked_prs(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        source = FakePRSource(OPEN_INFO)
        use_case = _make_use_case(source, reactions, repo, resolver)

        await use_case.execute()

        assert len(reactions.added) == 0

    async def test_single_pr_missing_emoji(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(), message_ref=_msg_ref()))
        source = FakePRSource(MERGED_INFO)
        use_case = _make_use_case(source, reactions, repo, resolver)

        await use_case.execute()

        assert len(reactions.added) == 1
        assert reactions.added[0] == (_msg_ref(), "git-merged")

    async def test_multiple_distinct_prs(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(1), message_ref=_msg_ref("C1", "1.0")))
        repo.stored.append(TrackedPR(pr_url=_pr_url(2), message_ref=_msg_ref("C2", "2.0")))
        source = FakePRSource(MERGED_INFO)
        use_case = _make_use_case(source, reactions, repo, resolver)

        await use_case.execute()

        assert len(reactions.added) == 2

    async def test_already_applied_emoji_is_idempotent(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                message_ref=_msg_ref(),
                applied_emojis=frozenset({"git-merged"}),
            )
        )
        source = FakePRSource(MERGED_INFO)
        use_case = _make_use_case(source, reactions, repo, resolver)

        await use_case.execute()

        assert len(reactions.added) == 0

    async def test_partial_failure_does_not_abort_others(
        self, reactions: FakeReactions, repo: FakePRRepository, resolver: FakeEmojiConfigResolver
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(1), message_ref=_msg_ref("C1", "1.0")))
        repo.stored.append(TrackedPR(pr_url=_pr_url(2), message_ref=_msg_ref("C2", "2.0")))
        repo.stored.append(TrackedPR(pr_url=_pr_url(3), message_ref=_msg_ref("C3", "3.0")))
        source = _FailingPRSource(MERGED_INFO, failing_numbers={2})
        exclusions = FakeUserExclusionRepo()
        webhook = HandleGitHubWebhook(
            source, reactions, repo, resolver, exclusions, FakeScopeSettingsRepo()
        )
        use_case = ReconcileTrackedPRs(pr_repository=repo, handle_webhook=webhook)

        await use_case.execute()

        # PR #2 fails silently (HandleGitHubWebhook catches fetch failures),
        # so all 3 PRs are attempted and the 2 that succeed get reactions
        assert len(reactions.added) == 2
