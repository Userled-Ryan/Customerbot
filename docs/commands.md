# Commands & interactions

customerbot exposes three surfaces in Slack: slash commands, the ticket
card's interactive buttons, and the message-event detector. The
in-app webhook is documented under [Configuration](configuration.md)
and the [Getting Started](getting-started.md#3-run-the-server) page.

## Slash commands

### `/log` (shortcut: `/l`)

Open the ticket-intake modal. `/l` is a one-keystroke alias for `/log`;
both behave identically. The variant that opens depends on the
invocation channel:

- **Inside `#tech-assistance`** → `csm_intake` modal (description, org,
  prod link, blocking radio, optional deadline, blocking-impact).
- **Anywhere else** → `se_bug` modal (org, source, summary, description,
  blocking Yes/No, optional deadline, optional affected user, optional
  replay link). Severity is derived from the blocking answer
  (Yes → `Blocking`, No → `Degraded`), matching the CSM intake flow; SE
  refines it via Reclassify if needed.

On submission the bot validates, runs dedupe, creates the ticket,
records the `null → New` event, drafts the §9a customer-facing
acknowledgement, and posts the ticket card to
`SE_TICKETS_CHANNEL_ID`. If `CUSTOMERBOT_SE_TICKETS_CHANNEL_ID` is
unset the ticket is still created — no card is posted.

### `/board`

On-demand snapshot. Responds ephemerally so it doesn't pollute shared
channels. Each `TIC-NNN` is a link to the ticket's card (falling back to
the original customer thread if the ticket has no card yet) — the same
linking the SLA and reply-needed alert DMs use, so you can jump straight
to the source.

| Usage | Renders |
|---|---|
| `/board` or `/board tickets` | Live tickets grouped by lane × status, priority-sorted within each bucket |
| `/board articles` | Articles grouped by `ArticleStatus` (Suggested → Live → Needs update → Rejected) with linked FAQ ticket refs |

Anything else returns an inline usage hint.

## Ticket-card buttons

Posted in `SE_TICKETS_CHANNEL_ID` on creation and re-rendered on every
state change. Always rendered as two action rows so Slack's per-row
button limit isn't bumped.

The card header reflects the ticket's lifecycle stage at a glance:
`:white_check_mark:` once it's awaiting-customer / resolved, and
`:lock:` with a struck-through title once it's closed. The fields block
lists the **Stakeholders** — the `@`-mentioned CSM(s) of the affected
org(s), pulled from `orgs.csm_user_id`, so the customer's CSM is looped
in and can follow progress without being the SE working the ticket.

### Primary row (live tickets)

| Button | Effect | Implementation |
|---|---|---|
| **Resolved** | Status → `Awaiting customer confirmation` · §9c draft DM | [`application/tracking/resolve.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/resolve.py) |
| **Resolved via hotfix** | Same status transition · auto-creates `Underlying bug` ticket on Dev Action lane · `ticket_links` `hotfix-of` row | same |
| **Move to Dev Action** | Lane → Dev Action · pings `@support` in `SUPPORT_PING_CHANNEL_ID` · appends OUTBOUND comms event | [`application/tracking/lane_handoff.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/lane_handoff.py) |
| **Reclassify** | Opens the §4c reclassify modal | [`application/tracking/reclassify.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/reclassify.py) |
| **Add affected org** | Opens an org-picker modal · adds the org · re-runs multi-customer bump check | [`application/tracking/add_affected_org.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/add_affected_org.py) |
| **Drop** | Status → `Closed` immediately (with an "are you sure?" confirm) · removes the ticket from the live set so all SLA / nudge / pre-close jobs stop · the manual counterpart to the 7-day auto-close | [`application/tracking/drop.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/drop.py) |

### Closed tickets

A closed/dropped card collapses to a single **Reopen** button (within
30d → `In progress`; older → DM suggests a new linked ticket). Reopen is
absent from live cards, where it would no-op.
[`application/tracking/reopen.py`](https://github.com/Userled-Ryan/Customerbot/blob/main/src/customerbot/application/tracking/reopen.py)

### Secondary row

| Button | Effect |
|---|---|
| **Set deadline** / **Change deadline** | Datepicker modal · empty submit clears |
| **Reply needed** / **Clear reply-needed** | Toggles the SE-set "waiting on a reply" flag · shows a `:speech_balloon: Reply needed` badge on the card · cleared by the SE once they've replied. Flagged tickets get a `:speech_balloon:` marker in the open-tickets digest below. |
| **Needs article** *(FAQ tickets only)* | Inserts an article in `Suggested` state and links it to the FAQ ticket |

### Open-tickets digest

Twice a day — **10:00 and 17:00 SE-local** — the bot DMs the SE a single
roll-up of the tickets needing their action: every live ticket in **New** or
**In progress** (Awaiting customer is excluded — the ball's in the customer's
court), sorted by priority with age and a link to the card. It carries counts
by tier and a `:speech_balloon:` marker on tickets flagged **Reply needed**,
and stays silent when nothing is open.

This is the only proactive ticket notification: the per-stage SLA escalation
DMs were removed in favour of this digest (the SLA clocks still tick silently
for reporting). The reply-needed flag has no auto-detection of replies (a
last-author heuristic mislabels cases like a CSM posting "raised this
internally"), so the SE clearing it is the authoritative "handled" signal.

## Reclassify modal

Five required fields:

| Field | Type | Notes |
|---|---|---|
| New type | Dropdown | Bug · Config · FAQ |
| New subtype | Dropdown | All nine subtypes listed; server-side validation rejects mismatches with a message pinned to the subtype block |
| Why reclassify | Multiline text | |
| Next step | Multiline text | |
| Owner | User picker | |

On Save: the ticket's type / subtype are updated, an
`event_reclassifications` row is appended, the card refreshes,
recipients are resolved (reporter + new owner + CSM of each affected
org + `@support` channel iff lane = Dev Action), and SE gets a DM
with the §9f draft and a **Send to stakeholders** / **Cancel** pair
of buttons. Send is required to deliver the internal alert — bot
never sends to customers.

## Customer-channel detector

The bot listens for `message` events from any channel that doesn't
start with `D` (i.e. not a DM):

- If the message contains `log` or `check` as a whole word (case-insensitive)
- And the sender is in `CUSTOMERBOT_INTERNAL_USER_GROUP_ID`
- And the thread isn't already linked to a live ticket
- And the message isn't a bot message

…the bot DMs the sender an **Open ticket form** card. Clicking it
opens the `se_bug` modal pre-filled with the thread permalink and a
description drafted from the last five messages. Suppression: messages
containing `no log` / `no check` are skipped.

`@CustomerBot log this` (or `@CustomerBot check this`) in a thread
reaches the same DM flow as a manual override.

## What the bot never does

- Sends a message to a customer channel/thread. Every customer-facing
  message is a draft the bot DMs SE.
- Mutates the event-log tables. The repository layer + the SQLite
  triggers in migration `0007_v1_ticket_schema` both reject any UPDATE
  / DELETE against `event_status_changes`, `event_prio_changes`,
  `event_reclassifications`, or `event_comms_log`.
- Auto-applies a priority bump. Multi-customer bumps, P0 candidate
  flags, and matrix-override suggestions all surface as DM cards with
  a button. Hard rule.
