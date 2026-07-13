"""Integration tests for `/report` — the resolved product-improvement summary.

Covers the resolved-in-window repo query, the product-improvement filter
(code-change OR feature-request, excluding config-only work), and both the
template fallback and an LLM-narrative path in `RenderReport`.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.tracking.render_report import RenderReport
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    ResolutionType,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from customerbot.domain.tracking.report import ReportItem, ReportNarrative


def _resolved(
    *,
    title: str,
    ticket_type: TicketType,
    subtype: TicketSubtype,
    resolution_type: ResolutionType | None,
    resolved_at: datetime,
) -> Ticket:
    return Ticket(
        title=title,
        type=ticket_type,
        subtype=subtype,
        status=TicketStatus.RESOLVED,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description=f"context for {title}",
        resolution_type=resolution_type,
        resolved_at=resolved_at,
    )


async def _seed(tickets: SQLiteTicketRepository) -> None:
    # In-window feature-request (Product change) — included.
    await tickets.create(
        _resolved(
            title="Add form block to the editor",
            ticket_type=TicketType.FEATURE_REQUEST,
            subtype=TicketSubtype.NEW_CAPABILITY,
            resolution_type=ResolutionType.CODE_CHANGE,
            resolved_at=datetime(2026, 7, 7, 10, 0),
        )
    )
    # In-window bug fixed via a code change — included.
    await tickets.create(
        _resolved(
            title="Fix checkout hang on submit",
            ticket_type=TicketType.BUG,
            subtype=TicketSubtype.PLATFORM_WIDE,
            resolution_type=ResolutionType.CODE_CHANGE,
            resolved_at=datetime(2026, 7, 8, 12, 0),
        )
    )
    # In-window config toggle, no code change — EXCLUDED.
    await tickets.create(
        _resolved(
            title="Enable feature flag for Acme",
            ticket_type=TicketType.CONFIG,
            subtype=TicketSubtype.SETUP_INTEGRATION,
            resolution_type=ResolutionType.NO_CODE_CHANGE,
            resolved_at=datetime(2026, 7, 8, 9, 0),
        )
    )
    # Feature-request resolved BEFORE the window — EXCLUDED.
    await tickets.create(
        _resolved(
            title="Old capability from last month",
            ticket_type=TicketType.FEATURE_REQUEST,
            subtype=TicketSubtype.ENHANCEMENT,
            resolution_type=ResolutionType.CODE_CHANGE,
            resolved_at=datetime(2026, 6, 15, 9, 0),
        )
    )


@pytest.mark.asyncio
async def test_query_resolved_between_bounds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await _seed(tickets)
    got = await tickets.query_resolved_between(
        datetime(2026, 7, 6, 0, 0), datetime(2026, 7, 10, 23, 59, 59)
    )
    titles = {t.title for t in got}
    # All three in-window resolved tickets are returned (filter is in the use case).
    assert "Add form block to the editor" in titles
    assert "Fix checkout hang on submit" in titles
    assert "Enable feature flag for Acme" in titles
    # The June ticket is outside the window.
    assert "Old capability from last month" not in titles


@pytest.mark.asyncio
async def test_report_template_fallback_filters_to_improvements(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await _seed(tickets)
    # summarizer=None → deterministic template.
    report = RenderReport(tickets=tickets, summarizer=None)
    blocks = await report.execute(start=date(2026, 7, 6), end=date(2026, 7, 10))
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # Included product improvements appear as bullets.
    assert "Add form block to the editor" in rendered
    assert "Fix checkout hang on submit" in rendered
    # Config-only toggle and the out-of-window ticket do not.
    assert "Enable feature flag for Acme" not in rendered
    assert "Old capability from last month" not in rendered
    # A short narrative summary is present.
    assert "product improvement" in rendered.lower()


@pytest.mark.asyncio
async def test_report_empty_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await _seed(tickets)
    report = RenderReport(tickets=tickets, summarizer=None)
    # A window with only the config toggle → no product improvements.
    blocks = await report.execute(start=date(2026, 7, 8), end=date(2026, 7, 8))
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # 8 July also has the code-change bug fix, so narrow to a truly empty day:
    empty = await report.execute(start=date(2026, 7, 1), end=date(2026, 7, 1))
    empty_text = "\n".join(b.get("text", {}).get("text", "") for b in empty if "text" in b)
    assert "No product improvements" in empty_text
    # (sanity) 8 July still surfaces the code-change bug fix.
    assert "Fix checkout hang on submit" in rendered


class _StubSummarizer:
    """Returns a fixed narrative — stands in for the Anthropic adapter."""

    def __init__(self) -> None:
        self.calls: list[list[ReportItem]] = []

    async def summarize(
        self, items: list[ReportItem], *, start: date, end: date
    ) -> ReportNarrative | None:
        self.calls.append(items)
        return ReportNarrative(
            summary="We shipped some lovely things this week.",
            bullets=["A brand new form block", "A snappier checkout"],
        )


@pytest.mark.asyncio
async def test_report_uses_llm_narrative_when_available(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await _seed(tickets)
    stub = _StubSummarizer()
    report = RenderReport(tickets=tickets, summarizer=stub)
    blocks = await report.execute(start=date(2026, 7, 6), end=date(2026, 7, 10))
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # The stub's prose + bullets are rendered.
    assert "lovely things" in rendered
    assert "A brand new form block" in rendered
    assert "A snappier checkout" in rendered
    # Only the two in-window product improvements were handed to the summarizer.
    assert len(stub.calls) == 1
    assert len(stub.calls[0]) == 2


@pytest.mark.asyncio
async def test_report_falls_back_when_summarizer_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tickets = SQLiteTicketRepository(session_factory)
    await _seed(tickets)

    class _NoneSummarizer:
        async def summarize(
            self, items: list[ReportItem], *, start: date, end: date
        ) -> ReportNarrative | None:
            return None

    report = RenderReport(tickets=tickets, summarizer=_NoneSummarizer())
    blocks = await report.execute(start=date(2026, 7, 6), end=date(2026, 7, 10))
    rendered = "\n".join(b.get("text", {}).get("text", "") for b in blocks if "text" in b)
    # Template bullets (real ticket titles) appear when the LLM declines.
    assert "Add form block to the editor" in rendered
