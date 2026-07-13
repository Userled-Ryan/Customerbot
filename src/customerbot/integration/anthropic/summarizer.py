"""Anthropic-backed `/report` narrative summariser.

Implements `ReportSummarizerPort`: turns the list of resolved product
improvements into a short customer-facing blurb + bullets. Fails soft — any
problem (SDK missing, network error, unparseable response) returns `None`, and
`RenderReport` falls back to its deterministic template. Never raises.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from customerbot.domain.tracking.report import ReportItem, ReportNarrative

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You write short, upbeat product-update notes for a B2B SaaS company's "
    "customers. You are given a list of product improvements shipped in a date "
    "range. Write copy suitable to paste directly into a customer Slack channel."
)

_INSTRUCTIONS = (
    "Return ONLY a JSON object (no markdown fences, no prose around it) with "
    "exactly these keys:\n"
    '  "summary": a 2–3 line plain-language overview of the improvements as a '
    "single string.\n"
    '  "bullets": an array of strings, one per improvement, each a concise '
    "customer-facing description.\n"
    "Rules: no internal ticket ids, no customer names, no PR/GitHub links, no "
    "engineering jargon. Focus on the customer-visible benefit. Keep it warm "
    "but concise."
)


class AnthropicReportSummarizer:
    """`ReportSummarizerPort` backed by the Anthropic Messages API."""

    def __init__(self, *, api_key: str, model: str = "claude-haiku-4-5") -> None:
        self._api_key = api_key
        self._model = model

    async def summarize(
        self, items: list[ReportItem], *, start: date, end: date
    ) -> ReportNarrative | None:
        if not items:
            return None
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            logger.warning("anthropic SDK not installed; using template fallback")
            return None

        prompt = _build_prompt(items, start=start, end=end)
        try:
            client = AsyncAnthropic(api_key=self._api_key)
            resp = await client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            logger.exception("Anthropic /report summary failed; using template fallback")
            return None

        text = "".join(
            getattr(block, "text", "")
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ).strip()
        return _parse_narrative(text)


def _build_prompt(items: list[ReportItem], *, start: date, end: date) -> str:
    lines = [
        f"Date range: {start.isoformat()} to {end.isoformat()}.",
        f"{len(items)} product improvement(s) shipped:",
        "",
    ]
    for i, item in enumerate(items, start=1):
        kind = "code change" if item.is_code_change else "product change"
        lines.append(f"{i}. [{item.subtype} · {kind}] {item.title}")
        if item.description.strip():
            lines.append(f"   context: {item.description.strip()[:500]}")
    lines += ["", _INSTRUCTIONS]
    return "\n".join(lines)


def _parse_narrative(text: str) -> ReportNarrative | None:
    """Parse the model's JSON reply, tolerating ```json fences. `None` on failure."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip a leading ```json / ``` fence and trailing ```.
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        cleaned = cleaned.removeprefix("json").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")].strip()
    try:
        data: Any = json.loads(cleaned)
    except (ValueError, TypeError):
        logger.warning("Anthropic /report reply was not valid JSON; using template fallback")
        return None
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary", "")).strip()
    raw_bullets = data.get("bullets", [])
    if not isinstance(raw_bullets, list):
        return None
    bullets = [str(b).strip() for b in raw_bullets if str(b).strip()]
    if not summary or not bullets:
        return None
    return ReportNarrative(summary=summary, bullets=bullets)
