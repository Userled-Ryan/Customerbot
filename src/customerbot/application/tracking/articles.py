"""FAQ → Article workflow (flow §9, min-spec §10d, plan Chunk 12).

Two surfaces:

- `CreateArticleFromFAQ` — handles the `Needs article` button click on a
  FAQ ticket card. Inserts an `articles` row in state `Suggested` (no
  triage yet), links it to the source FAQ ticket via `ticket_articles`,
  refreshes the ticket card, and DMs SE a confirmation with the new
  article's id. The FAQ ticket itself is unchanged — per flow §9a, FAQ
  tickets close as soon as the customer is answered, regardless of
  whether the article ships.

- `RenderArticlesBoard` — builds the Slack snapshot for the `/board
  articles` slash command. Groups articles by `ArticleStatus`, lists
  the linked FAQ ticket(s) per article so SE can navigate back, and
  surfaces a quick summary line at the top.

The Articles DB ownership is SE today (flow §9b). The bot only triggers
state transitions on the `Suggested` path; manual transitions to
Accepted / In progress / Live / Needs update / Rejected happen via
admin tooling that isn't part of the v1 chat surface.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Article
from customerbot.domain.tickets.ports import (
    ArticleRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import (
    ArticleStatus,
    TicketType,
)

logger = logging.getLogger(__name__)


_STATUS_DISPLAY: dict[ArticleStatus, str] = {
    ArticleStatus.SUGGESTED: ":bulb: Suggested",
    ArticleStatus.ACCEPTED: ":white_check_mark: Accepted",
    ArticleStatus.IN_PROGRESS: ":writing_hand: In progress",
    ArticleStatus.LIVE: ":green_book: Live",
    ArticleStatus.NEEDS_UPDATE: ":warning: Needs update",
    ArticleStatus.REJECTED: ":x: Rejected",
}

# Order the board renders. Suggested first because it's the active queue.
_STATUS_ORDER: tuple[ArticleStatus, ...] = (
    ArticleStatus.SUGGESTED,
    ArticleStatus.ACCEPTED,
    ArticleStatus.IN_PROGRESS,
    ArticleStatus.LIVE,
    ArticleStatus.NEEDS_UPDATE,
    ArticleStatus.REJECTED,
)


class CreateArticleFromFAQ:
    """Handle the `Needs article` button click on a FAQ ticket card."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        articles: ArticleRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        se_user_id: str,
    ) -> None:
        self._tickets = tickets
        self._articles = articles
        self._orgs = orgs
        self._slack = slack
        self._se_user_id = se_user_id

    async def execute(self, *, ticket_id: int, by_user_id: str) -> Article | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Needs article clicked on missing ticket %s", ticket_id)
            return None
        if ticket.type != TicketType.FAQ:
            # The button only renders on FAQ cards; clicking it from any other
            # context is almost certainly a stale-card replay or a race.
            logger.info(
                "Needs article clicked on non-FAQ ticket %s (type=%s) — ignoring",
                ticket.display_id,
                ticket.type.value,
            )
            return None
        article = await self._articles.create(
            Article(title=ticket.title, status=ArticleStatus.SUGGESTED, owner_user_id=by_user_id)
        )
        assert article.id is not None
        await self._articles.link_to_ticket(article.id, ticket.id)
        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)
        await self._slack.send_dm(
            self._se_user_id,
            (
                f":bulb: Article ART-{article.id:03d} suggested from "
                f"{ticket.display_id}: _{article.title}_."
            ),
        )
        return article


class RenderArticlesBoard:
    """Build the `/board articles` snapshot."""

    def __init__(
        self,
        articles: ArticleRepositoryPort,
        tickets: TicketRepositoryPort,
    ) -> None:
        self._articles = articles
        self._tickets = tickets

    async def execute(self) -> list[dict[str, Any]]:
        articles = await self._articles.list_all()
        if not articles:
            return [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":books: *Articles board* — _no articles yet._",
                    },
                }
            ]
        by_status: dict[ArticleStatus, list[Article]] = defaultdict(list)
        for article in articles:
            by_status[article.status].append(article)

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":books: *Articles board* — *{len(articles)}* article(s) "
                        f"across {sum(1 for s in _STATUS_ORDER if by_status.get(s))} state(s)."
                    ),
                },
            },
            {"type": "divider"},
        ]
        for status in _STATUS_ORDER:
            bucket = by_status.get(status)
            if not bucket:
                continue
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{_STATUS_DISPLAY[status]}* — {len(bucket)} article(s)",
                    },
                }
            )
            for article in bucket:
                assert article.id is not None
                linked_ticket_ids = await self._articles.list_linked_tickets(article.id)
                linked_display = await self._render_linked_tickets(linked_ticket_ids)
                title = article.url and f"<{article.url}|{article.title}>" or article.title
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"• *ART-{article.id:03d}* {title}\n  Linked: {linked_display}"
                            ),
                        },
                    }
                )
            blocks.append({"type": "divider"})
        return blocks

    async def _render_linked_tickets(self, ticket_ids: list[int]) -> str:
        if not ticket_ids:
            return "_no linked tickets_"
        rendered: list[str] = []
        for ticket_id in ticket_ids:
            ticket = await self._tickets.get(ticket_id)
            rendered.append(ticket.display_id if ticket is not None else f"TIC-{ticket_id:03d}")
        return ", ".join(rendered)
