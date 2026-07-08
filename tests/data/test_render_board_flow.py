"""Integration tests for the on-demand `/board` snapshot.

`RenderTicketsBoard` groups live tickets by lane × status with
priority-ordered lines; the empty case renders a single block.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.render_board import RenderTicketsBoard
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


def _bug(
    *,
    priority: Priority = Priority.P2,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    lane: Lane | None = Lane.SE_ACTION,
    title: str = "checkout broken",
    created_at: datetime = datetime(2026, 5, 25, 9, 0),
    deadline: date | None = None,
) -> Ticket:
    return Ticket(
        title=title,
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        status=status,
        lane=lane,
        priority=priority,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="users hang on submit",
        created_at=created_at,
        deadline=deadline,
    )


@pytest.mark.asyncio
async def test_board_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    assert len(blocks) == 1
    assert "no live tickets" in blocks[0]["text"]["text"]


@pytest.mark.asyncio
async def test_board_groups_by_lane_and_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    await orgs.upsert(Org(id="acme", name="Acme"))
    se_new = await tickets.create(
        _bug(status=TicketStatus.NEW, lane=Lane.SE_ACTION, title="se-new")
    )
    se_inp = await tickets.create(
        _bug(
            status=TicketStatus.IN_PROGRESS,
            lane=Lane.SE_ACTION,
            title="se-in-progress",
            priority=Priority.P1,
        )
    )
    dev_inp = await tickets.create(
        _bug(status=TicketStatus.IN_PROGRESS, lane=Lane.DEV_ACTION, title="dev-in-progress")
    )
    # Closed shouldn't appear.
    await tickets.create(_bug(status=TicketStatus.CLOSED, title="closed"))
    assert se_new.id and se_inp.id and dev_inp.id
    await tickets.add_org(se_new.id, "acme")

    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # Lane labels present.
    assert "SE Action" in rendered
    assert "Dev Action" in rendered
    # All three live ticket titles present; the closed one isn't.
    assert "se-new" in rendered
    assert "se-in-progress" in rendered
    assert "dev-in-progress" in rendered
    assert "closed" not in rendered
    # The org name rendered for se_new (which has acme linked).
    assert "Acme" in rendered


@pytest.mark.asyncio
async def test_board_shows_deadline_days_remaining(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    today = datetime.now(UTC).date()
    await tickets.create(
        _bug(title="due-soon", deadline=today + timedelta(days=3))
    )
    await tickets.create(
        _bug(title="due-today", lane=Lane.DEV_ACTION, deadline=today)
    )
    await tickets.create(
        _bug(title="overdue", lane=None, deadline=today - timedelta(days=2))
    )
    await tickets.create(_bug(title="no-deadline", priority=Priority.P1))

    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    assert "due in 3d" in rendered
    assert "due today" in rendered
    assert "overdue 2d" in rendered
    # A ticket without a deadline gets no deadline segment.
    no_deadline_line = next(
        line for line in rendered.splitlines() if "no-deadline" in line
    )
    assert "due" not in no_deadline_line
    assert "overdue" not in no_deadline_line


@pytest.mark.asyncio
async def test_board_splits_large_status_group_under_slack_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A status group with many tickets must not produce a >3000-char section.

    Slack rejects the whole message (`invalid_blocks`) if any single section's
    text exceeds 3000 chars, so long groups are split across section blocks.
    """
    tickets = SQLiteTicketRepository(session_factory)
    orgs = SQLiteOrgRepository(session_factory)
    # 40 tickets in one lane×status group easily exceeds a single section's cap.
    for i in range(40):
        await tickets.create(
            _bug(
                status=TicketStatus.NEW,
                lane=Lane.SE_ACTION,
                title=f"ticket number {i} with a reasonably long descriptive title",
            )
        )

    board = RenderTicketsBoard(tickets=tickets, orgs=orgs, workspace_url="https://test.slack.com")
    blocks = await board.execute()
    section_lens = [
        len(b["text"]["text"]) for b in blocks if b["type"] == "section"
    ]
    assert section_lens, "expected section blocks"
    assert all(n <= 3000 for n in section_lens), section_lens
    # All 40 tickets still rendered across the split blocks.
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    for i in range(40):
        assert f"ticket number {i} " in rendered
