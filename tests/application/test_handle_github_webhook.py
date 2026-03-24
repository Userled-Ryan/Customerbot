import pytest

from prbot.application.handle_github_webhook import HandleGitHubWebhook
from prbot.config import EmojiConfig
from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import MessageRef, PRInfo, PRUrl, Review, ReviewState
from tests.conftest import FakePRRepository, FakePRSource, FakeReactions


def _pr_url() -> PRUrl:
    return PRUrl(owner="o", repo="r", number=1)


def _msg_ref(channel: str = "C123", ts: str = "1234.5678") -> MessageRef:
    return MessageRef(integration_id="slack", ref=f"{channel}:{ts}")


EMOJI = EmojiConfig()


class TestHandleGitHubWebhook:
    @pytest.fixture
    def reactions(self) -> FakeReactions:
        return FakeReactions()

    @pytest.fixture
    def repo(self) -> FakePRRepository:
        return FakePRRepository()

    async def test_webhook_adds_emoji_for_new_status(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(), message_ref=_msg_ref()))
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        source = FakePRSource(merged_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(reactions.added) == 1
        assert reactions.added[0] == (_msg_ref(), "git-merged")

    async def test_webhook_skips_already_applied_emoji(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                message_ref=_msg_ref(),
                applied_emojis=frozenset({"git-merged"}),
            )
        )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        source = FakePRSource(merged_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(reactions.added) == 0

    async def test_webhook_no_reaction_for_open_status(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(TrackedPR(pr_url=_pr_url(), message_ref=_msg_ref()))
        open_info = PRInfo(state="open", merged=False, reviews=())
        source = FakePRSource(open_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(reactions.added) == 0

    async def test_webhook_updates_all_tracked_messages(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        for i in range(3):
            repo.stored.append(
                TrackedPR(pr_url=_pr_url(), message_ref=_msg_ref(f"C{i}", f"{i}.0000"))
            )
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        source = FakePRSource(merged_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(reactions.added) == 3

    async def test_webhook_for_untracked_pr(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        merged_info = PRInfo(state="closed", merged=True, reviews=())
        source = FakePRSource(merged_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 999)

        assert len(reactions.added) == 0

    async def test_webhook_adds_approval_to_existing_emojis(
        self, reactions: FakeReactions, repo: FakePRRepository
    ) -> None:
        repo.stored.append(
            TrackedPR(
                pr_url=_pr_url(),
                message_ref=_msg_ref(),
                applied_emojis=frozenset({"speech_balloon"}),
            )
        )
        approved_info = PRInfo(
            state="open",
            merged=False,
            reviews=(Review(user_login="alice", state=ReviewState.APPROVED),),
        )
        source = FakePRSource(approved_info)
        use_case = HandleGitHubWebhook(source, reactions, repo, EMOJI)

        await use_case.execute("o", "r", 1)

        assert len(reactions.added) == 1
        assert reactions.added[0][1] == "git-approved"
