"""On-demand product report (`/report`).

Rolls up the product improvements *resolved* in a date range into a short,
customer-facing summary + bullets an SE can paste straight into a customer
channel. Deliberately narrow: only work that represents a real product change
is included — a code-change resolution OR a "Product change" (feature-request)
ticket — so config-only work like enabling a feature flag for one org is left
out.

The narrative is written by an optional `ReportSummarizerPort` (an LLM). When
none is wired, or it declines/fails (returns `None`), a deterministic template
renders instead, so `/report` always produces something safe to share.

Pure-ish: the only I/O is the resolved-ticket query and the (optional)
summariser call; the handler owns posting the returned blocks.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from customerbot.application.tracking.render_board import _section_blocks
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import TicketRepositoryPort
from customerbot.domain.tickets.value_objects import ResolutionType, TicketType
from customerbot.domain.tracking.report import (
    ReportItem,
    ReportNarrative,
    ReportSummarizerPort,
)


class RenderReport:
    """Build the `/report` product-improvement summary for a date range."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        summarizer: ReportSummarizerPort | None,
    ) -> None:
        self._tickets = tickets
        self._summarizer = summarizer

    async def execute(self, *, start: date, end: date) -> list[dict[str, Any]]:
        # Span the whole end day so tickets resolved on `end` are included.
        start_dt = datetime.combine(start, time.min)
        end_dt = datetime.combine(end, time.max)
        resolved = await self._tickets.query_resolved_between(start_dt, end_dt)
        improvements = [t for t in resolved if _is_product_improvement(t)]

        if not improvements:
            return [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":sparkles: *Product improvements* — {_range_label(start, end)}\n"
                            "_No product improvements were resolved in this window._"
                        ),
                    },
                }
            ]

        items = [_to_item(t) for t in improvements]
        narrative: ReportNarrative | None = None
        if self._summarizer is not None:
            narrative = await self._summarizer.summarize(items, start=start, end=end)
        if narrative is None:
            narrative = _template_narrative(items)

        return _render_blocks(narrative, start=start, end=end)


def _is_product_improvement(ticket: Ticket) -> bool:
    """A resolved ticket counts as a product improvement when it shipped code
    OR is a "Product change" (feature-request) — not a config-only tweak."""
    return (
        ticket.resolution_type == ResolutionType.CODE_CHANGE
        or ticket.type == TicketType.FEATURE_REQUEST
    )


def _to_item(ticket: Ticket) -> ReportItem:
    return ReportItem(
        title=ticket.title,
        description=ticket.description,
        subtype=ticket.subtype.value,
        is_code_change=ticket.resolution_type == ResolutionType.CODE_CHANGE,
        pr_link=ticket.resolution_pr_link,
    )


def _template_narrative(items: list[ReportItem]) -> ReportNarrative:
    """Deterministic fallback when no LLM summary is available.

    Keeps to titles only — no internal ids, customer names, or PR links — so
    the output is safe to paste into a customer channel as-is.
    """
    n = len(items)
    summary = (
        f"Here's a summary of the {n} product improvement{'s' if n != 1 else ''} "
        "we shipped over this period. These are live for everyone."
    )
    bullets = [item.title.strip() for item in items if item.title.strip()]
    return ReportNarrative(summary=summary, bullets=bullets)


def _render_blocks(narrative: ReportNarrative, *, start: date, end: date) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":sparkles: *Product improvements* — {_range_label(start, end)}",
            },
        },
        {
            "type": "section",
            # Slack caps a section at 3000 chars; a 2–3 line summary is well under.
            "text": {"type": "mrkdwn", "text": narrative.summary[:2900]},
        },
        {"type": "divider"},
    ]
    bullet_lines = [f"• {b.strip()}" for b in narrative.bullets if b.strip()]
    # `_section_blocks` packs bullets into multiple sections so none trips
    # Slack's 3000-char-per-section cap (which drops the whole message).
    blocks.extend(_section_blocks("", bullet_lines))
    return blocks


def _range_label(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d %b %Y")
    return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
