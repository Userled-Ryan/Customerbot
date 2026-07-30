"""SE → Dev Action handoff (flow §2b, plan Chunk 9).

Triggered when SE clicks `Move to Dev Action` on the ticket card. The bot:

1. Flips the ticket's `Lane` to `Dev Action`.
2. Records the *dev owner* — the current member of the `support_handle`
   user-group (preferring one with a Linear mapping, since that's who an
   assign will actually land on).
3. Opens/advances the Linear mirror (`ensure_open_for_dev`) so the issue is a
   real, open dev issue in the Product Responder project — this is where the
   dev works from, so it happens first and gives us a live Linear deep link —
   then puts the issue in the dev's name (`sync_assignee`).
4. DMs every current member of the `support_handle` user-group the pre-filled
   handoff payload (repro from the description, affected customers, prio,
   original Slack link, screenshot/replay, and the Linear link). The support
   group *is* the dev on duty, so we message them directly rather than pinging
   `@support` in a channel — the DM lands with the person who'll pick it up.
5. Marks the ticket-card feed: reacts 🛠️ on the card and posts a threaded
   "moved to Dev Action" reply so the handoff is visible in the tickets feed.
6. Appends an `OUTBOUND` comms-log row so the handoff is auditable.
7. Refreshes the ticket card so the lane + dev-owner labels update.

Lane and dev owner are the only ticket mutations here — status stays as-is, and
the SE owner is left alone so the card keeps showing both. Idempotent click: if
the ticket is already on Dev Action lane, the bot still re-DMs the dev (SE may
want to nudge) and re-resolves the dev owner (the rotation may have moved on),
but the audit row records that no lane change happened.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from customerbot.application.intake.ticket_card import refresh_card
from customerbot.application.linear.sync import LinearSync
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import (
    EventLogRepositoryPort,
    OrgRepositoryPort,
    TicketRepositoryPort,
)
from customerbot.domain.tickets.value_objects import CommsDirection, Lane

logger = logging.getLogger(__name__)

# Card-feed markers for the handoff. The reaction sits on the card message and
# the reply threads under it, so the tickets feed shows the handoff at a glance
# alongside the lane label the card refresh already flips to "Dev Action".
DEV_HANDOFF_REACTION = "hammer_and_wrench"  # 🛠️ — handed off to engineering
MOVED_TO_DEV_THREAD_REPLY = (
    ":hammer_and_wrench: Moved to *Dev Action* — handed off to the dev on support."
)
RETURNED_TO_SE_THREAD_REPLY = (
    ":leftwards_arrow_with_hook: Returned to *SE Action* — back with Solutions Eng."
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MoveToDevAction:
    """Handle the `Move to Dev Action` button click."""

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        support_handle: str | None,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._support_handle = support_handle
        self._linear = linear

    async def execute(self, *, ticket_id: int, by_user_id: str) -> Ticket | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Move to Dev clicked on missing ticket %s", ticket_id)
            return None

        now = _utcnow()
        if ticket.lane != Lane.DEV_ACTION:
            await self._tickets.update_lane(ticket.id, Lane.DEV_ACTION, now=now)

        # Who's on support right now. Fetched once and reused for both the dev
        # owner and the DM fan-out, so the person we assign in Linear is
        # guaranteed to be one of the people we message.
        members = await self._support_group_members(ticket)
        dev = self._pick_dev(members)
        # An unresolvable dev (group unset/empty) leaves any previously recorded
        # one in place — better a stale dev than none at all. `Return to SE` is
        # what clears the field.
        if dev is not None and dev != ticket.dev_owner_user_id:
            await self._tickets.update_dev_owner(ticket.id, dev, now=now)

        # Linear mirror next: this is where the issue becomes a real, open dev
        # issue in the Product Responder project for engineers to pick up, in the
        # dev's own name. Doing it before the DM means the handoff message
        # carries a live Linear link.
        if self._linear is not None:
            await self._linear.ensure_open_for_dev(ticket.id)
            await self._linear.sync_assignee(ticket.id)

        # Re-fetch so the DM sees the persisted dev owner + Linear id/identifier/url.
        ticket = await self._tickets.get(ticket.id) or ticket

        org_ids = await self._tickets.list_orgs(ticket.id)
        org_names = await self._org_names(org_ids)

        recipients = await self._dm_dev_on_support(ticket, org_names, members)
        await self._mark_feed(ticket)

        await self._events.append_comms(
            ticket_id=ticket.id,
            direction=CommsDirection.OUTBOUND,
            channel="dm:dev-on-support" if recipients else "bot",
            sender_user_id=by_user_id,
            message_link=None,
            at=now,
            note="lane-handoff:se->dev",
        )

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        refreshed = await self._tickets.get(ticket.id)
        return refreshed

    async def _org_names(self, org_ids: list[str]) -> list[str]:
        names: list[str] = []
        for org_id in org_ids:
            org = await self._orgs.get(org_id)
            names.append(org.name if org else org_id)
        return names

    async def _support_group_members(self, ticket: Ticket) -> list[str]:
        """Current members of the support user-group — the dev(s) on duty.

        Empty when the handle isn't configured or the group has nobody in it; the
        handoff still goes through (lane flips), it just can't name a dev.
        """
        if not self._support_handle:
            logger.warning(
                "SUPPORT_HANDLE not configured — ticket %s moved to dev but no dev DM'd",
                ticket.display_id,
            )
            return []
        members = await self._slack.list_group_members(self._support_handle)
        if not members:
            logger.warning(
                "Support group %s has no members — ticket %s moved to dev but no dev DM'd",
                self._support_handle,
                ticket.display_id,
            )
        return members

    def _pick_dev(self, members: list[str]) -> str | None:
        """The dev to put the Linear issue in the name of.

        Prefers a group member with a Linear mapping — assigning an unmapped one
        is a silent no-op (`assign_issue` returns False), so we'd end up with the
        SE still on the issue. Falls back to the first member so the card at
        least names someone. The group is the rotating on-duty responder, so in
        practice there's one candidate.
        """
        if not members:
            return None
        if self._linear is None:
            return members[0]
        mapped = self._linear.first_mapped(members)
        if mapped is None:
            logger.warning(
                "No member of support group %s has a Linear user mapping — "
                "the issue will stay with the SE",
                self._support_handle,
            )
            return members[0]
        return mapped

    async def _dm_dev_on_support(
        self, ticket: Ticket, org_names: list[str], members: list[str]
    ) -> list[str]:
        """DM the handoff payload to every current member of the support group.

        The support user-group is the dev(s) on duty, so each member gets the
        ticket direct. Returns the list of user-ids messaged (empty if the group
        isn't configured or has no members — the lane still changed either way).
        """
        if not members:
            return []
        blocks = handoff_blocks(ticket, org_names)
        for user_id in members:
            await self._slack.send_dm_blocks(
                user_id,
                blocks,
                text=f"{ticket.display_id} handed off to dev",
            )
        return members

    async def _mark_feed(self, ticket: Ticket) -> None:
        """Signal the handoff in the tickets feed: react on the card + reply in
        its thread. Best-effort; skipped if the ticket has no card."""
        if not ticket.card_channel_id or not ticket.card_message_ts:
            return
        reply = MOVED_TO_DEV_THREAD_REPLY
        if ticket.dev_owner_user_id:
            reply += f" <@{ticket.dev_owner_user_id}> owns it in Linear."
        if ticket.linear_issue_url:
            label = ticket.linear_issue_identifier or "Linear issue"
            reply += f"\n:link: <{ticket.linear_issue_url}|{label}>"
        await self._slack.add_reaction(
            ticket.card_channel_id, ticket.card_message_ts, DEV_HANDOFF_REACTION
        )
        await self._slack.send_message(
            ticket.card_channel_id, reply, thread_ts=ticket.card_message_ts
        )


def handoff_blocks(
    ticket: Ticket,
    affected_org_names: list[str],
) -> list[dict[str, Any]]:
    """Pre-filled dev-handoff DM payload (flow §2b).

    Sent direct to the dev on support, so it leads with the Linear link they'll
    work from and drops the `@support` group mention (they're already the
    recipient). Names the recorded dev owner so a group with more than one member
    knows whose name the Linear issue is in.
    """
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "—"
    repro = ticket.description.strip() or "_no repro steps captured — see thread_"
    fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Priority*\n{ticket.priority.value}"},
        {"type": "mrkdwn", "text": f"*Severity*\n{ticket.severity.value}"},
        {"type": "mrkdwn", "text": f"*Affected orgs*\n{orgs_text}"},
        {"type": "mrkdwn", "text": f"*Source*\n{ticket.source.value}"},
    ]
    context_bits: list[str] = []
    if ticket.original_slack_link:
        context_bits.append(f"<{ticket.original_slack_link}|Original thread>")
    if ticket.replay_link:
        context_bits.append(f"<{ticket.replay_link}|Link>")
    if ticket.screenshot_url:
        context_bits.append(f"<{ticket.screenshot_url}|Screenshot>")

    if ticket.dev_owner_user_id:
        ownership = (
            f"You're the dev on support — this ticket is now on the *Dev Action* lane, "
            f"assigned to <@{ticket.dev_owner_user_id}> in Linear."
        )
    else:
        ownership = "You're the dev on support — this ticket is now on the *Dev Action* lane."
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":arrow_right: *{ticket.display_id} handed off to you* (_{ticket.title}_)\n"
                    + ownership
                ),
            },
        },
        {"type": "section", "fields": fields},
    ]
    # The Linear issue is where the dev works from, so surface it prominently
    # right under the header rather than tucked into the context footer.
    if ticket.linear_issue_url:
        label = ticket.linear_issue_identifier or "View in Linear"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":hammer_and_wrench: *Work from Linear:* "
                        f"<{ticket.linear_issue_url}|{label}>"
                    ),
                },
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Repro / context:*\n{repro[:2500]}",
            },
        }
    )
    if context_bits:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " · ".join(context_bits)}],
            }
        )
    return blocks


class ReturnToSEAction:
    """Handle the `Return to SE` button click — the inverse of `MoveToDevAction`.

    Undoes a dev handoff when it becomes clear a dev isn't needed: flips the
    lane back to `SE Action`, clears the dev owner (so the Linear issue goes back
    into the SE owner's name), pulls the Linear mirror back off the dev board
    (In Progress → Triage for a still-`NEW` ticket, via `sync_state`), tells the
    dev(s) on support it's back with Solutions Eng, and clears the 🛠️ handoff
    marker in the tickets feed. Status is untouched — only the lane flips back.
    """

    def __init__(
        self,
        tickets: TicketRepositoryPort,
        events: EventLogRepositoryPort,
        orgs: OrgRepositoryPort,
        slack: SlackPort,
        support_handle: str | None,
        linear: LinearSync | None = None,
    ) -> None:
        self._tickets = tickets
        self._events = events
        self._orgs = orgs
        self._slack = slack
        self._support_handle = support_handle
        self._linear = linear

    async def execute(self, *, ticket_id: int, by_user_id: str) -> Ticket | None:
        ticket = await self._tickets.get(ticket_id)
        if ticket is None or ticket.id is None:
            logger.warning("Return to SE clicked on missing ticket %s", ticket_id)
            return None

        now = _utcnow()
        if ticket.lane != Lane.SE_ACTION:
            await self._tickets.update_lane(ticket.id, Lane.SE_ACTION, now=now)
        # No dev owns it any more — clearing this before `sync_assignee` is what
        # puts the Linear issue back in the SE owner's name.
        if ticket.dev_owner_user_id is not None:
            await self._tickets.update_dev_owner(ticket.id, None, now=now)

        # Pull the mirror back to match the reverted lane (In Progress → Triage
        # for a still-NEW ticket). `sync_state` recomputes from status+lane, so
        # it stays consistent with the reconcile sweep. Resolve still owns Done.
        # Move the issue back into the SE Responder project too, so the SE Linear
        # view mirrors the lane (the inverse of MoveToDev's dev-project add), and
        # hand the assignee back to the SE.
        if self._linear is not None:
            await self._linear.sync_state(ticket.id)
            await self._linear.ensure_in_se_project(ticket.id)
            await self._linear.sync_assignee(ticket.id)

        ticket = await self._tickets.get(ticket.id) or ticket

        recipients = await self._dm_dev_on_support(ticket)
        await self._clear_feed_marker(ticket)

        await self._events.append_comms(
            ticket_id=ticket.id,
            direction=CommsDirection.OUTBOUND,
            channel="dm:dev-on-support" if recipients else "bot",
            sender_user_id=by_user_id,
            message_link=None,
            at=now,
            note="lane-handoff:dev->se",
        )

        await refresh_card(self._slack, self._tickets, self._orgs, ticket.id)

        refreshed = await self._tickets.get(ticket.id)
        return refreshed

    async def _dm_dev_on_support(self, ticket: Ticket) -> list[str]:
        """Tell every current support-group member the handoff is undone."""
        if not self._support_handle:
            return []
        members = await self._slack.list_group_members(self._support_handle)
        if not members:
            return []
        blocks = returned_to_se_blocks(ticket)
        for user_id in members:
            await self._slack.send_dm_blocks(
                user_id,
                blocks,
                text=f"{ticket.display_id} back with Solutions Eng",
            )
        return members

    async def _clear_feed_marker(self, ticket: Ticket) -> None:
        """Clear the 🛠️ handoff marker and note the return in the card thread."""
        if not ticket.card_channel_id or not ticket.card_message_ts:
            return
        await self._slack.remove_reaction(
            ticket.card_channel_id, ticket.card_message_ts, DEV_HANDOFF_REACTION
        )
        await self._slack.send_message(
            ticket.card_channel_id,
            RETURNED_TO_SE_THREAD_REPLY,
            thread_ts=ticket.card_message_ts,
        )


def returned_to_se_blocks(ticket: Ticket) -> list[dict[str, Any]]:
    """DM payload for an undone dev handoff.

    Strikes through the ticket reference (the dev's Linear link when we have
    one) so the earlier "work this" ask reads as cancelled, then states plainly
    that it's back with Solutions Eng.
    """
    if ticket.linear_issue_url:
        label = ticket.linear_issue_identifier or ticket.display_id
        struck = f"~<{ticket.linear_issue_url}|{label} · {ticket.title}>~"
    else:
        struck = f"~{ticket.display_id} · {ticket.title}~"
    text = f"{struck}\n*Back with Solutions Eng* — no dev needed for now."
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
