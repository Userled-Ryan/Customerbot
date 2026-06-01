from __future__ import annotations

from datetime import UTC, datetime

import pytest

from customerbot.application.bot_state.sweeper import SweepEphemeralState


class _CountingRepo:
    """Records the `now` passed and returns a configured deletion count."""

    def __init__(self, count: int) -> None:
        self._count = count
        self.last_now: datetime | None = None
        self.calls = 0

    async def delete_expired(self, *, now: datetime) -> int:
        self.last_now = now
        self.calls += 1
        return self._count


@pytest.mark.asyncio
async def test_sweeper_sums_deletions_across_four_repos() -> None:
    drafts = _CountingRepo(2)
    dedupe = _CountingRepo(3)
    prio = _CountingRepo(0)
    reclassify = _CountingRepo(1)

    sweeper = SweepEphemeralState(
        drafts=drafts,  # type: ignore[arg-type]
        pending_dedupe=dedupe,  # type: ignore[arg-type]
        pending_prio=prio,  # type: ignore[arg-type]
        pending_reclassify=reclassify,  # type: ignore[arg-type]
    )

    total = await sweeper.execute()
    assert total == 6
    assert drafts.calls == dedupe.calls == prio.calls == reclassify.calls == 1


@pytest.mark.asyncio
async def test_sweeper_passes_the_same_now_to_every_repo() -> None:
    drafts = _CountingRepo(0)
    dedupe = _CountingRepo(0)
    prio = _CountingRepo(0)
    reclassify = _CountingRepo(0)
    sweeper = SweepEphemeralState(
        drafts=drafts,  # type: ignore[arg-type]
        pending_dedupe=dedupe,  # type: ignore[arg-type]
        pending_prio=prio,  # type: ignore[arg-type]
        pending_reclassify=reclassify,  # type: ignore[arg-type]
    )

    fixed = datetime(2026, 5, 29, 12, 0, 0)
    await sweeper.execute(now=fixed)

    assert drafts.last_now == fixed
    assert dedupe.last_now == fixed
    assert prio.last_now == fixed
    assert reclassify.last_now == fixed


@pytest.mark.asyncio
async def test_sweeper_default_now_is_naive_utc() -> None:
    drafts = _CountingRepo(0)
    dedupe = _CountingRepo(0)
    prio = _CountingRepo(0)
    reclassify = _CountingRepo(0)
    sweeper = SweepEphemeralState(
        drafts=drafts,  # type: ignore[arg-type]
        pending_dedupe=dedupe,  # type: ignore[arg-type]
        pending_prio=prio,  # type: ignore[arg-type]
        pending_reclassify=reclassify,  # type: ignore[arg-type]
    )

    before = datetime.now(UTC).replace(tzinfo=None)
    await sweeper.execute()
    after = datetime.now(UTC).replace(tzinfo=None)

    assert drafts.last_now is not None
    assert drafts.last_now.tzinfo is None  # naive
    assert before <= drafts.last_now <= after
