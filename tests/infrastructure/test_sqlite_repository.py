import pytest

from prbot.domain.entities import TrackedPR
from prbot.domain.value_objects import PRUrl
from prbot.infrastructure.sqlite_repository import SQLitePRRepository


@pytest.fixture
async def repository() -> SQLitePRRepository:
    repo = SQLitePRRepository(db_path=":memory:")
    await repo.initialize()
    return repo


def _pr_url(number: int = 1) -> PRUrl:
    return PRUrl(owner="octocat", repo="hello", number=number)


def _tracked(
    number: int = 1,
    channel: str = "C123",
    ts: str = "1234.5678",
    emojis: frozenset[str] = frozenset(),
) -> TrackedPR:
    return TrackedPR(
        pr_url=_pr_url(number),
        channel_id=channel,
        message_ts=ts,
        applied_emojis=emojis,
    )


class TestSQLitePRRepository:
    async def test_save_and_find(self, repository: SQLitePRRepository) -> None:
        tracked = _tracked(emojis=frozenset({"eyes"}))
        await repository.save(tracked)

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 1
        assert results[0].pr_url == tracked.pr_url
        assert results[0].channel_id == "C123"
        assert results[0].message_ts == "1234.5678"
        assert results[0].applied_emojis == frozenset({"eyes"})

    async def test_upsert_on_conflict(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(emojis=frozenset({"eyes"})))
        await repository.save(_tracked(emojis=frozenset({"eyes", "white_check_mark"})))

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 1
        assert results[0].applied_emojis == frozenset({"eyes", "white_check_mark"})

    async def test_add_emoji(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(emojis=frozenset({"eyes"})))

        await repository.add_emoji(_pr_url(), "C123", "1234.5678", "tada")

        results = await repository.find_by_pr_url(_pr_url())
        assert results[0].applied_emojis == frozenset({"eyes", "tada"})

    async def test_find_returns_empty_for_unknown(self, repository: SQLitePRRepository) -> None:
        results = await repository.find_by_pr_url(_pr_url(999))
        assert len(results) == 0

    async def test_multiple_messages_for_same_pr(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked(channel="C1", ts="1.0"))
        await repository.save(_tracked(channel="C2", ts="2.0"))
        await repository.save(_tracked(channel="C1", ts="3.0"))

        results = await repository.find_by_pr_url(_pr_url())
        assert len(results) == 3

    async def test_save_with_no_emojis(self, repository: SQLitePRRepository) -> None:
        await repository.save(_tracked())

        results = await repository.find_by_pr_url(_pr_url())
        assert results[0].applied_emojis == frozenset()
