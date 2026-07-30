"""Ticket-card Slack message builder.

The v1 replacement for the Notion board (decision #5). Each ticket gets one
Slack message in the configured `SE_TICKETS_CHANNEL_ID` channel; the bot
`chat.update`s the same message on every state change so the card is always
the live view.

Block-rendering is pure (no I/O). `refresh_card` ties everything together
for the Chunk-9 lifecycle handlers that mutate ticket state and need the
card to reflect the change.
"""

from __future__ import annotations

import logging
from typing import Any

from customerbot.application.intake.se_owner_actions import (
    ACTION_SET_SE_OWNER,
    SeOwnerChangePayload,
)
from customerbot.application.priority.actions import (
    ACTION_SET_PRIORITY,
    REASON_MANUAL_OVERRIDE,
    PriorityChangePayload,
)
from customerbot.domain.messaging.ports import SlackPort
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.ports import OrgRepositoryPort, TicketRepositoryPort
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    ResolutionType,
    TicketStatus,
    TicketSubtype,
    TicketType,
)

logger = logging.getLogger(__name__)

ACTION_MOVE_TO_DEV = "ticket_move_to_dev"
ACTION_RETURN_TO_SE = "ticket_return_to_se"
ACTION_RESOLVED = "ticket_resolved"
ACTION_RECLASSIFY = "ticket_reclassify"
ACTION_REOPEN = "ticket_reopen"
ACTION_DROP = "ticket_drop"
ACTION_ADD_AFFECTED_ORG = "ticket_add_affected_org"
ACTION_NEEDS_ARTICLE = "ticket_needs_article"
ACTION_SET_DEADLINE = "ticket_set_deadline"
ACTION_TOGGLE_REPLY_NEEDED = "ticket_toggle_reply_needed"
ACTION_SET_STAKEHOLDER = "ticket_set_stakeholder"
ACTION_TOGGLE_PLATFORM_WIDE = "ticket_toggle_platform_wide"

# SE-owner dropdown candidates (Slack user ids), configured once at startup from
# `settings.se_owner_user_ids` via `configure_se_owner_ids`. The card redraw path
# (`refresh_card`) reads this to build the *SE owner* select without threading
# config through all ~15 `refresh_card` call sites; `build_blocks` stays pure by
# receiving the resolved (id, display-name) options as an argument.
_SE_OWNER_IDS: list[str] = []


def configure_se_owner_ids(ids: list[str]) -> None:
    """Set the SE-owner dropdown candidates. Called once from `main` at startup."""
    global _SE_OWNER_IDS
    _SE_OWNER_IDS = list(ids)


async def resolve_se_owner_options(
    slack: SlackPort, current_owner_id: str | None
) -> list[tuple[str, str]]:
    """Resolve `(user_id, display_name)` for the SE-owner dropdown.

    Always includes the ticket's current owner (even if not a configured
    candidate) so the select's `initial_option` is valid. Names are resolved via
    Slack (cached in the gateway), so this is cheap on repeat renders.
    """
    ids = list(_SE_OWNER_IDS)
    if current_owner_id and current_owner_id not in ids:
        ids.append(current_owner_id)
    return [(uid, await slack.get_user_display_name(uid)) for uid in ids]


_STATUS_LABEL: dict[TicketStatus, str] = {
    TicketStatus.NEW: "New",
    TicketStatus.IN_PROGRESS: "In progress",
    TicketStatus.AWAITING_CUSTOMER: "Awaiting customer",
    TicketStatus.RESOLVED: "Resolved",
    TicketStatus.CLOSED: "Closed",
}

_LANE_LABEL: dict[Lane, str] = {
    Lane.SE_ACTION: "SE Action",
    Lane.DEV_ACTION: "Dev Action",
}

_PRIORITY_EMOJI: dict[Priority, str] = {
    Priority.P0: ":rotating_light:",
    Priority.P1: ":red_circle:",
    Priority.P2: ":large_orange_circle:",
    Priority.P3: ":large_yellow_circle:",
    Priority.P4: ":white_circle:",
}

# Header prefix per status so the card reads its lifecycle stage at a glance.
# New / In progress carry no prefix (the default working state); the
# "wrapping up" states get a check, and a dropped/closed ticket gets a lock
# so it's unmistakable from the live ones in the channel.
_STATUS_HEADER_EMOJI: dict[TicketStatus, str] = {
    TicketStatus.AWAITING_CUSTOMER: ":white_check_mark: ",
    TicketStatus.RESOLVED: ":white_check_mark: ",
    TicketStatus.CLOSED: ":lock: ",
}

# A resolved or dropped ticket is terminal: its card collapses to a single
# struck line (header + affected org) plus a Reopen button, so it takes up
# minimal room when scrolling the channel.
_RETIRED_STATUSES: frozenset[TicketStatus] = frozenset({TicketStatus.RESOLVED, TicketStatus.CLOSED})

_RESOLUTION_LABEL: dict[ResolutionType, str] = {
    ResolutionType.NO_CODE_CHANGE: "No code change",
    ResolutionType.CODE_CHANGE: "Code change",
}


def _strike(text: str) -> str:
    """Wrap each non-empty line in `~…~` so a retired card reads as struck
    through. Slack strikethrough doesn't span newlines, so we strike per line;
    `:emoji:` shortcodes and `<link|label>` mrkdwn still render inside it."""
    return "\n".join(f"~{line}~" if line.strip() else line for line in text.split("\n"))


def build_blocks(
    ticket: Ticket,
    affected_org_names: list[str],
    csm_user_ids: list[str] | None = None,
    se_owner_options: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Return the Block-Kit blocks for the ticket card.

    Buttons are always rendered; their handlers no-op until Chunk 9. The button
    `value` carries the ticket id so handlers can route without state lookups.

    `csm_user_ids` are the CSMs of the affected org(s); they're rendered as
    @-mention stakeholders on the card so the customer's CSM is looped in and
    can follow the ticket's progress without being the SE.
    """
    prio_emoji = _PRIORITY_EMOJI[ticket.priority]
    status_label = _STATUS_LABEL[ticket.status]
    lane_label = _LANE_LABEL[ticket.lane] if ticket.lane else "—"
    orgs_text = ", ".join(affected_org_names) if affected_org_names else "_no orgs linked_"
    # De-dupe while preserving order — one CSM may own multiple affected orgs.
    stakeholders = list(dict.fromkeys(csm_user_ids or []))
    stakeholders_text = ", ".join(f"<@{uid}>" for uid in stakeholders) if stakeholders else "—"

    # A resolved/dropped ticket is terminal: strike every line of text so the
    # whole card reads as visually retired (not just the title). The leading
    # status emoji is kept outside the strike so it still renders.
    retired = ticket.status in _RETIRED_STATUSES
    s = _strike if retired else _identity

    header_prefix = _STATUS_HEADER_EMOJI.get(ticket.status, "")
    header_text = f"{header_prefix}{s(f'*{ticket.display_id} · {ticket.title}*')}"

    # A retired card is only glanced at while scrolling the channel, so collapse
    # it to a single line — the struck header plus the affected org(s), kept
    # un-struck so it stays legible — with just the Reopen action. The full
    # detail is one click away via the original thread or by reopening. The
    # "Resolved via" note (with PR link) rides along as a small context line so
    # the CSM-alert record stays visible without expanding the card.
    if retired:
        value = str(ticket.id) if ticket.id is not None else ""
        org_suffix = f" · *{orgs_text}*" if affected_org_names else ""
        collapsed: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": header_text + org_suffix}},
        ]
        # Kept un-struck: dropping a ticket is silent to the customer, so this
        # link is the SE's route back to the thread to reply themselves.
        if ticket.original_slack_link:
            collapsed.append(
                _context_line(f":link: <{ticket.original_slack_link}|Original thread>")
            )
        if ticket.resolution_type is not None:
            label = _RESOLUTION_LABEL[ticket.resolution_type]
            pr = f" (<{ticket.resolution_pr_link}|PR>)" if ticket.resolution_pr_link else ""
            collapsed.append(_context_line(f":hammer_and_wrench: *Resolved via:* {label}{pr}"))
        collapsed.append({"type": "actions", "elements": [_button("Reopen", ACTION_REOPEN, value)]})
        return collapsed

    metadata_text = (
        f"{prio_emoji} *{ticket.priority.value}* · "
        f":label: {ticket.type.value} / {ticket.subtype.value} · "
        f"*{status_label}*"
    )
    if ticket.lane is not None:
        metadata_text += f" · :traffic_light: {lane_label}"

    if ticket.is_urgent:
        deadline_text = ":rotating_light: Urgent — no deadline"
    elif ticket.deadline:
        deadline_text = ticket.deadline.strftime("%a %d %b %Y")
    else:
        deadline_text = "—"
    field_lines = [
        f"*Severity*\n{ticket.severity.value}",
        f"*Source*\n{ticket.source.value}",
        f"*Reporter*\n<@{ticket.reporter_user_id}>",
        f"*SE owner*\n{f'<@{ticket.se_owner_user_id}>' if ticket.se_owner_user_id else '—'}",
        f"*Stakeholders*\n{stakeholders_text}",
        f"*Affected orgs*\n{orgs_text}",
        f"*Deadline*\n{deadline_text}",
    ]
    # Only on a handed-off ticket, so SE-lane cards stay as they were. The SE
    # owner line above stays put — both are shown, since the SE still owns the
    # customer relationship while the dev owns the Linear issue.
    if ticket.dev_owner_user_id:
        field_lines.append(f"*Dev owner*\n<@{ticket.dev_owner_user_id}>")
    # `affected_user` is intake-collected but was previously dropped from the
    # card — surface it alongside the other metadata when set.
    if ticket.affected_user:
        field_lines.append(f"*Affected user*\n{ticket.affected_user}")
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": s(metadata_text)}},
    ]

    # Urgent badge — a drop-everything ticket awaiting first action. Suppressed
    # once it moves to In progress / Resolved (is_urgent guards on NEW).
    if ticket.is_urgent:
        blocks.append(_context_line(":rotating_light: *URGENT* — no deadline; needs action now"))

    # The Original thread link is the SE's primary way back to the customer
    # conversation, so it sits right under the header/metadata rather than
    # buried near the bottom of the card.
    if ticket.original_slack_link:
        blocks.append(_context_line(s(f":link: <{ticket.original_slack_link}|Original thread>")))

    blocks.append(
        {
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": s(line)} for line in field_lines],
        }
    )

    # SE-set "waiting on a reply" badge — only meaningful while the ticket is
    # live, so it's suppressed on retired cards.
    if ticket.reply_needed and not retired:
        blocks.append(_context_line(":speech_balloon: *Reply needed*"))

    if ticket.blocking_impact:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": s(f"*Impact*\n{ticket.blocking_impact}")},
            }
        )

    if ticket.description:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": s(_truncate_for_section(ticket.description))},
            }
        )

    # Intake-collected reference links — each rendered only when present so
    # cards stay tidy.
    if ticket.replay_link:
        blocks.append(_context_line(s(f":link: <{ticket.replay_link}|Link>")))
    if ticket.prod_link:
        blocks.append(_context_line(s(f":link: <{ticket.prod_link}|In product>")))
    if ticket.campaign_url:
        blocks.append(_context_line(s(f":mega: <{ticket.campaign_url}|Campaign>")))
    if ticket.screenshot_url:
        blocks.append(_context_line(s(f":framed_picture: <{ticket.screenshot_url}|Screenshot>")))

    # Reopen is retired-only (it no-ops on a live ticket) and is rendered on the
    # collapsed retired card above, so the live button set below omits it.
    value = str(ticket.id) if ticket.id is not None else ""
    # The dev-handoff button is a toggle: once on the Dev Action lane it becomes
    # "Return to SE" so an SE can undo the handoff if a dev turns out not to be
    # needed. The card refresh re-renders this whenever the lane flips.
    lane_button = (
        _button("Return to SE", ACTION_RETURN_TO_SE, value)
        if ticket.lane == Lane.DEV_ACTION
        else _button("Move to Dev Action", ACTION_MOVE_TO_DEV, value)
    )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button("Resolved", ACTION_RESOLVED, value),
                lane_button,
                _button("Reclassify", ACTION_RECLASSIFY, value),
                _button("Add affected org", ACTION_ADD_AFFECTED_ORG, value),
                _drop_button(value),
            ],
        }
    )
    # Secondary actions row: the Set P-level select (lets the SE re-prioritise
    # straight from the card — the card refresh + Linear mirror are handled by
    # ApplyPriorityChange), then deadline, then the reply-needed toggle, plus
    # the FAQ-only "Needs article" when applicable. Kept separate from the
    # primary button row so Slack doesn't wrap them into a less-readable layout.
    secondary_elements: list[dict[str, Any]] = []
    # The select needs the ticket id to encode its change payload, so it's
    # skipped on the (never rendered in practice) id-less card.
    if ticket.id is not None:
        secondary_elements.append(_set_priority_select(ticket.id, ticket.priority))
        # SE-owner dropdown — only when candidates were resolved (Slack up + at
        # least one configured owner). Refreshing/mirroring is handled by
        # ApplySeOwnerChange, exactly like the priority select.
        if se_owner_options:
            secondary_elements.append(
                _set_se_owner_select(ticket.id, ticket.se_owner_user_id, se_owner_options)
            )
    secondary_elements += [
        _button(
            "Set deadline" if ticket.deadline is None else "Change deadline",
            ACTION_SET_DEADLINE,
            value,
        ),
        _button(
            "Clear reply-needed" if ticket.reply_needed else "Reply needed",
            ACTION_TOGGLE_REPLY_NEEDED,
            value,
        ),
        _button("Set stakeholder", ACTION_SET_STAKEHOLDER, value),
    ]
    # Bug tickets get a one-click platform-wide/customer-specific toggle (the
    # same subtype the reclassify modal edits, surfaced for the common case).
    if ticket.type == TicketType.BUG:
        toggle_label = (
            "Mark customer-specific"
            if ticket.subtype == TicketSubtype.PLATFORM_WIDE
            else "Mark platform-wide"
        )
        secondary_elements.append(_button(toggle_label, ACTION_TOGGLE_PLATFORM_WIDE, value))
    if ticket.type == TicketType.FAQ:
        secondary_elements.append(_button("Needs article", ACTION_NEEDS_ARTICLE, value))
    blocks.append({"type": "actions", "elements": secondary_elements})

    return blocks


def _identity(text: str) -> str:
    return text


def _context_line(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _button(label: str, action_id: str, value: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": value,
    }


def _set_priority_select(ticket_id: int, current: Priority) -> dict[str, Any]:
    """`Set P-level` dropdown for the card.

    One `static_select` (a single element ⇒ a single, unique `action_id`, so no
    `invalid_blocks` risk). Each option carries the full priority-change payload
    in its `value`; selecting one routes through the same `ACTION_SET_PRIORITY`
    handler as the override-DM buttons, which applies the change and updates the
    card + Linear. P0 is offered here so everything is controllable from the
    channel — unlike the matrix-override DM, which excludes it by spec §5a.
    """

    def _option(prio: Priority) -> dict[str, Any]:
        return {
            "text": {
                "type": "plain_text",
                "text": f"{_PRIORITY_EMOJI[prio]} {prio.value}",
                "emoji": True,
            },
            "value": PriorityChangePayload(
                ticket_id=ticket_id, priority=prio, reason=REASON_MANUAL_OVERRIDE
            ).encode(),
        }

    options = [_option(prio) for prio in Priority]
    return {
        "type": "static_select",
        "action_id": ACTION_SET_PRIORITY,
        "placeholder": {"type": "plain_text", "text": "Set P-level", "emoji": True},
        "initial_option": _option(current),
        "options": options,
    }


def _set_se_owner_select(
    ticket_id: int, current: str | None, options: list[tuple[str, str]]
) -> dict[str, Any]:
    """`SE owner` dropdown for the card.

    One `static_select` (a single element ⇒ a single, unique `action_id`), so
    no `invalid_blocks` risk. Each option's `value` carries the full owner-change
    payload; selecting one routes through `ACTION_SET_SE_OWNER`, which reassigns
    the owner and updates the card + Linear assignee. `options` is `(id, name)`
    pairs — the display name is needed because a `<@id>` mention can't render
    inside a `static_select` label.
    """

    def _option(user_id: str, name: str) -> dict[str, Any]:
        return {
            "text": {"type": "plain_text", "text": name, "emoji": True},
            "value": SeOwnerChangePayload(ticket_id=ticket_id, owner_user_id=user_id).encode(),
        }

    select: dict[str, Any] = {
        "type": "static_select",
        "action_id": ACTION_SET_SE_OWNER,
        "placeholder": {"type": "plain_text", "text": "SE owner", "emoji": True},
        "options": [_option(uid, name) for uid, name in options],
    }
    current_name = next((name for uid, name in options if uid == current), None)
    if current is not None and current_name is not None:
        select["initial_option"] = _option(current, current_name)
    return select


def _drop_button(value: str) -> dict[str, Any]:
    """`Drop` closes the ticket. It's destructive (stops every reminder and
    retires the card), so it carries a native Slack confirmation dialog —
    nothing happens until the SE confirms."""
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": "Drop"},
        "action_id": ACTION_DROP,
        "value": value,
        "style": "danger",
        "confirm": {
            "title": {"type": "plain_text", "text": "Drop this ticket?"},
            "text": {
                "type": "mrkdwn",
                "text": (
                    "This closes the ticket and stops all reminders. The customer "
                    "isn't told — the *Original thread* link stays on the card so "
                    "you can reply to them yourself.\n"
                    "You can *Reopen* it within 30 days if more context appears."
                ),
            },
            "confirm": {"type": "plain_text", "text": "Drop"},
            "deny": {"type": "plain_text", "text": "Cancel"},
        },
    }


def _truncate_for_section(text: str, limit: int = 2900) -> str:
    """Slack section text max is 3000 chars; leave a small margin."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def fallback_text(ticket: Ticket) -> str:
    """Plain-text fallback for the message (notifications, screenreaders)."""
    return f"{ticket.display_id} {ticket.title} ({ticket.priority.value} · {ticket.status.value})"


async def refresh_card(
    slack: SlackPort,
    tickets: TicketRepositoryPort,
    orgs: OrgRepositoryPort,
    ticket_id: int,
) -> None:
    """Re-render the ticket card from current state and `chat.update` it.

    No-op if the ticket has no card (e.g. SE_TICKETS_CHANNEL_ID wasn't set
    when the ticket was created). Safe to call after any state change.
    """
    ticket = await tickets.get(ticket_id)
    if ticket is None or not ticket.card_channel_id or not ticket.card_message_ts:
        return
    org_ids = await tickets.list_orgs(ticket_id)
    org_names: list[str] = []
    csm_user_ids: list[str] = []
    for org_id in org_ids:
        org = await orgs.get(org_id)
        org_names.append(org.name if org else org_id)
        if org is not None and org.csm_user_id:
            csm_user_ids.append(org.csm_user_id)
    se_owner_options = await resolve_se_owner_options(slack, ticket.se_owner_user_id)
    blocks = build_blocks(ticket, org_names, csm_user_ids, se_owner_options)
    await slack.update_message(
        ticket.card_channel_id,
        ticket.card_message_ts,
        blocks,
        text=fallback_text(ticket),
    )


async def notify_csms_status_change(
    slack: SlackPort,
    tickets: TicketRepositoryPort,
    orgs: OrgRepositoryPort,
    ticket: Ticket,
    *,
    status_label: str,
    by_user_id: str,
    detail: str | None = None,
) -> None:
    """DM each affected org's CSM that the ticket reached a terminal state.

    Shared by `ResolveTicket` and `DropTicket` so the org→CSM lookup isn't
    duplicated. CSM ids are de-duped (one CSM may own several affected orgs);
    if the ticket has no CSM this silently does nothing.
    """
    if ticket.id is None:
        return
    csm_ids: list[str] = []
    for org_id in await tickets.list_orgs(ticket.id):
        org = await orgs.get(org_id)
        if org is not None and org.csm_user_id and org.csm_user_id not in csm_ids:
            csm_ids.append(org.csm_user_id)
    if not csm_ids:
        return
    detail_suffix = f" — {detail}" if detail else ""
    text = (
        f"*{ticket.display_id} · {ticket.title}* was marked *{status_label}* "
        f"by <@{by_user_id}>{detail_suffix}."
    )
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    for csm_id in csm_ids:
        await slack.send_dm_blocks(csm_id, blocks, text=f"{ticket.display_id} {status_label}")
