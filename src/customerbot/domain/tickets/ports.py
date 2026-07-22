from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from customerbot.domain.tickets.entities import Article, Org, Ticket
from customerbot.domain.tickets.value_objects import (
    CommsDirection,
    Lane,
    Priority,
    ResolutionType,
    TicketLinkRelation,
    TicketStatus,
    TicketSubtype,
    TicketType,
)


class TicketRepositoryPort(Protocol):
    async def create(self, ticket: Ticket) -> Ticket: ...

    async def get(self, ticket_id: int) -> Ticket | None: ...

    async def update_status(
        self,
        ticket_id: int,
        status: TicketStatus,
        *,
        now: datetime,
    ) -> None: ...

    async def update_priority(
        self, ticket_id: int, priority: Priority, *, now: datetime
    ) -> None: ...

    async def update_lane(self, ticket_id: int, lane: Lane, *, now: datetime) -> None: ...

    async def update_se_owner(
        self, ticket_id: int, user_id: str | None, *, now: datetime
    ) -> None: ...

    async def count_open_by_se_owner(self) -> dict[str, int]:
        """Count live-status tickets grouped by `se_owner_user_id`.

        Powers the balanced round-robin default owner on ticket creation —
        new tickets go to the SE with the fewest currently-open tickets.
        Owners with no open tickets are simply absent from the map.
        """
        ...

    async def update_card_message(
        self, ticket_id: int, channel_id: str, message_ts: str
    ) -> None: ...

    async def set_linear_issue(
        self, ticket_id: int, *, issue_id: str, identifier: str, url: str
    ) -> None: ...

    async def find_by_linear_issue_id(self, issue_id: str) -> Ticket | None: ...

    async def update_feature(self, ticket_id: int, feature: str | None) -> None: ...

    async def update_deadline(
        self,
        ticket_id: int,
        deadline: date | None,
        *,
        now: datetime,
    ) -> None: ...

    async def set_reply_needed(
        self,
        ticket_id: int,
        reply_needed: bool,
        *,
        now: datetime,
    ) -> None: ...

    async def set_resolution(
        self,
        ticket_id: int,
        resolution_type: ResolutionType,
        pr_link: str | None,
        *,
        now: datetime,
    ) -> None: ...

    async def update_type_subtype(
        self,
        ticket_id: int,
        ticket_type: TicketType,
        subtype: TicketSubtype,
        *,
        now: datetime,
    ) -> None: ...

    async def query_live(self) -> list[Ticket]: ...

    async def query_resolved_between(self, start: datetime, end: datetime) -> list[Ticket]:
        """Tickets whose `resolved_at` falls within `[start, end]`, oldest first.

        Keyed on `resolved_at` (not current status) so a ticket resolved then
        later reopened/closed within the window still counts as "solved during
        the window". Bounds are inclusive.
        """
        ...

    async def find_by_slack_link(self, slack_link: str) -> Ticket | None: ...

    async def add_org(self, ticket_id: int, org_id: str) -> None: ...

    async def list_orgs(self, ticket_id: int) -> list[str]: ...

    async def add_link(
        self, from_ticket_id: int, to_ticket_id: int, relation: TicketLinkRelation
    ) -> None: ...

    async def link_support_thread(
        self,
        ticket_id: int,
        channel_id: str,
        thread_ts: str,
        *,
        by_user_id: str | None,
        now: datetime,
    ) -> None:
        """Attach a support thread to a ticket. Idempotent; a thread already
        attached to another ticket is reassigned (the "move")."""
        ...

    async def list_support_threads(self, ticket_id: int) -> list[tuple[str, str]]:
        """Return `(channel_id, thread_ts)` for every thread attached to a ticket."""
        ...

    async def find_ticket_id_by_support_thread(self, channel_id: str, thread_ts: str) -> int | None:
        """The ticket a support thread is currently attached to, or None."""
        ...


class OrgRepositoryPort(Protocol):
    async def upsert(self, org: Org) -> None: ...

    async def get(self, org_id: str) -> Org | None: ...

    async def find_by_slack_channel(self, slack_channel_id: str) -> Org | None: ...

    async def list_all(self) -> list[Org]: ...


class ArticleRepositoryPort(Protocol):
    async def create(self, article: Article) -> Article: ...

    async def get(self, article_id: int) -> Article | None: ...

    async def link_to_ticket(self, article_id: int, ticket_id: int) -> None: ...

    async def list_linked_tickets(self, article_id: int) -> list[int]: ...

    async def list_all(self) -> list[Article]: ...


class EventLogRepositoryPort(Protocol):
    """Append-only writes for the four event-log tables.

    All methods INSERT only. Implementations MUST raise on any UPDATE or DELETE.
    """

    async def append_status_change(
        self,
        ticket_id: int,
        from_status: TicketStatus | None,
        to_status: TicketStatus,
        by_user_id: str | None,
        at: datetime,
        note: str = "",
    ) -> None: ...

    async def append_prio_change(
        self,
        ticket_id: int,
        from_priority: Priority | None,
        to_priority: Priority,
        by_user_id: str | None,
        at: datetime,
        reason: str = "",
    ) -> None: ...

    async def append_reclassification(
        self,
        ticket_id: int,
        from_type: TicketType,
        to_type: TicketType,
        from_subtype: TicketSubtype,
        to_subtype: TicketSubtype,
        by_user_id: str | None,
        at: datetime,
        reason: str,
        next_step: str,
        owner_user_id: str,
    ) -> int:
        """Append a reclassification event and return its row id.

        The id is captured so `pending_reclassify_sends.reclassification_event_id`
        can point at it — that lets the audit trail tie a sent internal alert
        back to the exact reclassification event it announces.
        """
        ...

    async def append_comms(
        self,
        ticket_id: int,
        direction: CommsDirection,
        channel: str,
        sender_user_id: str | None,
        message_link: str | None,
        at: datetime,
        note: str = "",
    ) -> None: ...

    async def last_status_change_into(
        self,
        ticket_id: int,
        to_status: TicketStatus,
    ) -> datetime | None:
        """Timestamp of the most recent transition INTO `to_status`, or None.

        Read-only convenience over the append-only event log; used by the SLA
        / auto-close jobs to compute "how long has this ticket been in status
        X" without denormalising onto the tickets table.
        """
        ...
