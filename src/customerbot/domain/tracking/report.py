"""Product-report summarisation port + DTOs (`/report`).

`/report` rolls up the product improvements shipped in a date range into a
short, customer-facing blurb an SE can paste into a customer channel. The
narrative can be written by an LLM (see the Anthropic adapter) or, when no
summariser is wired / the call fails, by a deterministic template — so the
port deliberately allows returning `None` to mean "fall back to the template".

Kept vendor-free (no Slack / SDK imports) so it lives in the domain layer.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel


class ReportItem(BaseModel, frozen=True):
    """One resolved product improvement, distilled for the summary."""

    title: str
    description: str
    subtype: str  # e.g. "new-capability" / "enhancement"
    is_code_change: bool
    pr_link: str | None = None


class ReportNarrative(BaseModel, frozen=True):
    """A summariser's output: a short blurb + one bullet per improvement."""

    summary: str  # 2–3 line narrative
    bullets: list[str]  # customer-facing, one product improvement each


class ReportSummarizerPort(Protocol):
    async def summarize(
        self, items: list[ReportItem], *, start: date, end: date
    ) -> ReportNarrative | None:
        """Return a narrative for `items`, or `None` to fall back to the template.

        Implementations must never raise for an unavailable backend (missing
        key, network error, bad response) — they return `None` so `/report`
        always renders something.
        """
        ...
