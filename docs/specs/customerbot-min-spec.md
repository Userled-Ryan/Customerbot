# CustomerBot — Min Spec (v1)

**Status:** Build target for the v1 of CustomerBot.
**Companion to:** `se-ticketing-flow-v1.md` (source-of-truth flow).
**Last updated:** 2026-05-29

This document defines what CustomerBot must do to operate the v1 SE ticketing flow. Build to this spec, no further. Anything beyond this is v2.

---

## 1. Operating principles

1. **Silent to customers.** The bot never posts publicly in customer channels and never DMs customers.
2. **Suggests, doesn't decide.** Bot acts unilaterally only on internal state changes and draft generation. Anything that links tickets, changes priority, or surfaces to a customer requires SE confirmation.
3. **Garbage-in is the failure mode.** Forced dropdowns over free-text wherever the value must be canonical (orgs, types, severity).
4. **No phantom tickets.** If a draft form isn't submitted within 30 minutes, drop it silently.
5. **Always log metadata.** Every state change writes an event row. Reports depend on this.

---

## 2. Inputs the bot must handle

### 2a. Slack listeners

| Surface | Listening for | Action |
|---|---|---|
| Customer channels | Internal-member messages containing `log` or `check` (case-insensitive, word boundary) | DM SE the pre-filled SE/bug form |
| Customer channels | `@CustomerBot log this` | Same as above (manual override) |
| `#tech-assistance` | `/log-ticket` slash command | Open CSM intake form |
| Any DM | `/log-ticket` slash command | Open SE/bug form, source = `DM`, on-behalf-of = other party |
| In-app webhook | Bug submission event | Create ticket directly with org/user/screenshot/replay |

### 2b. Buttons on tickets

Surfaced inline on each ticket (Notion buttons or Slack message buttons depending on context):

- `Move to Dev Action` — triggers handoff to `@support`
- `Resolved` — moves to Awaiting customer confirmation
- `Resolved via hotfix` — same + auto-creates linked underlying-bug ticket
- `Reclassify` — opens reclassify modal (new type, reason, next step, owner)
- `Reopen` — for tickets within 30-day reopen window
- `Add affected org` — opens org dropdown; bot suggests prio bump if thresholds hit

### 2c. Scheduled jobs

| Job | Cadence |
|---|---|
| SLA state recompute (green / amber / red) | Every 15 min |
| SLA amber/breach DM to SE | On state transition only, once per ticket per state |
| Auto-close `Awaiting customer confirmation` after 7d | Daily |
| Weekly digest to internal channel | Mondays 09:00 |
| Prio matrix refresh from source data | Weekly |

---

## 3. Trigger detection rules

### 3a. Customer channel — `log` / `check` detector

**Match:** message author is in the internal Slack workspace (SE, CSM, AE, eng) AND message text contains `log` or `check` as a standalone word (regex: `\b(log|check)\b`, case-insensitive).

**Suppress when:**
- Message is in a thread already linked to a live ticket (avoid double-logging)
- Message author is the bot itself
- Message contains `no log` / `no check` (negation handling, basic)

**On match:**
1. DM the message author with the pre-filled SE/bug form.
2. Form includes: thread permalink, channel→org mapping (auto-fill Org dropdown), draft description from the last 5 messages in the thread.
3. 30-minute submission window. If not submitted, drop.

**Anti-false-positive:** because `check` and `log` are common, expect noise. The 30-min drop rule absorbs it. Track false-positive rate in reporting; if >20% after 2 weeks, narrow the trigger set.

### 3b. `#tech-assistance` — form-only

No passive reading. Bot only acts on `/log-ticket` invocations.

### 3c. In-app submissions

Webhook payload required:
```
{
  org_id: string,
  user_id: string,
  user_email: string,
  page_url: string,
  description: string,
  screenshot_url: string,
  session_replay_url: string,
  reported_at: timestamp
}
```

Bot creates ticket directly with `Source: in-app`, runs dedupe, posts a feed entry in `#tech-assistance` for visibility.

---

## 4. Form payloads

### 4a. CSM intake form (`#tech-assistance`)

```yaml
modal_id: csm_intake
title: "Log a ticket"
fields:
  - id: description
    type: textarea
    label: "What's going on?"
    required: true
  - id: org
    type: dropdown
    source: orgs_db
    label: "Which customer?"
    required: true
  - id: prod_link
    type: url
    label: "Link to campaign or area in product"
    required: true
  - id: blocking
    type: radio
    options: [yes, no]
    label: "Is this blocking?"
    required: true
  - id: deadline
    type: date
    label: "Campaign go-live / deadline (optional)"
    required: false
conditional:
  if: blocking == yes
  then:
    - id: blocking_impact
      type: text
      label: "What's the impact?"
      required: true
```

### 4b. SE / bug form

```yaml
modal_id: se_bug
title: "Log ticket"
fields:
  - id: org
    type: dropdown
    source: orgs_db
    required: true
  - id: source
    type: dropdown
    options: [customer-channel, DM, call, email, in-app, tech-assistance]
    required: true
  - id: summary
    type: text
    max_length: 140
    required: true
  - id: description
    type: textarea
    required: false
  - id: blocking
    type: radio
    options: [yes, no]
    required: true
    note: severity derived — yes → blocking, no → degraded
  - id: deadline
    type: datepicker
    required: false
    note: when it must be fixed by, if blocking
  - id: affected_user
    type: text
    required: false
  - id: screenshot
    type: file_upload
    required: false
  - id: replay_link
    type: url
    required: false
prefill_from_context:
  - reporter (auto from invoking user)
  - original_slack_link (auto)
  - description (drafted from thread context if triggered from customer channel)
```

### 4c. Reclassify modal

```yaml
modal_id: reclassify
fields:
  - id: new_type
    type: dropdown
    options: [Bug, Config, FAQ]
    required: true
  - id: new_subtype
    type: dropdown
    source: subtypes_for(new_type)
    required: true
  - id: reason
    type: textarea
    required: true
  - id: next_step
    type: textarea
    required: true
  - id: owner
    type: person_picker
    required: true
```

---

## 5. Ticket creation pipeline

```
form submitted
    ↓
[1] Validate required fields
    ↓
[2] Run dedupe check against live tickets
    ├─ match found → bot DMs SE: "looks like TIC-042. Merge?" → SE confirms → append to existing
    └─ no match → continue
    ↓
[3] Compute suggested priority
    customer_weight = orgs_db[org_id].computed_weight
    severity_input  = "blocking" if form.blocking else "degraded"
    suggested_prio  = prio_matrix[customer_weight][severity_input]
    ↓
[4] Write ticket row to Tickets DB
    initial status = New
    initial lane   = SE Action (bugs only)
    ↓
[5] Write Status changes event row (null → New)
    ↓
[6] Draft initial customer-ack message → DM to SE
    ↓
[7] Post ticket card in #tech-assistance feed
```

---

## 6. Dedupe logic

**Scope:** live tickets only (any status except Closed).

**Match criteria** (any one is a candidate):
1. Same `org_id` + token-overlap score ≥ 0.6 on summary/description
2. Same `prod_link` (exact match)
3. Same `severity` + same feature tag + token-overlap score ≥ 0.7 across any org (cross-customer)

**On candidate match:**
- Bot DMs SE / submitter:
  > "This looks like **TIC-042** (*[title]*, *[type/prio]*, opened *[time ago]*, affecting *[orgs]*). Merge?"
- Two buttons: `Merge into TIC-042` / `Create new`
- If merged: append new context as a comment on TIC-042; add new org to `Affected orgs` if different; trigger prio-bump suggestion if §5c thresholds hit.

**Never** auto-merge.

---

## 7. Priority assignment

### 7a. On creation

`prio_matrix.lookup(customer_weight, severity) → suggested_prio`

Bot sets `Priority = suggested_prio` and DMs SE with the rationale:
> "TIC-091 set to **P2**. Customer weight: *medium* (ACV: mid-tier, sentiment: neutral, renewal: 2026-09). Severity: *degraded*. Override with [P1] [P2] [P3]."

SE can click a different tier — bot logs a `Prio changes` event with reason `manual override`.

### 7b. Multi-customer bumps

When a 2nd+ org is added to `Affected orgs`:

```python
n = len(affected_orgs)
current = ticket.priority
if n == 2:        suggest = bump_one_tier(current)
elif n >= 3:      suggest = min(current, "P1")
elif n >= 5 and is_critical_path(feature):
                  suggest = "P0"
```

Bot DMs SE: *"TIC-091 now affects 3 customers — suggest bump P2 → P1. Confirm?"*. SE clicks confirm or skip.

### 7c. P0

Bot **never sets P0**. It can post a candidate flag:
> "5 customers hit similar issue in 6h on critical-path feature *Publishing*. Consider P0?"

Only SE or CTO clicking the manual button sets P0.

---

## 8. SLA tracking

### 8a. State machine per ticket

```
elapsed = now - created_at
target  = sla_target[priority][stage]   # e.g. first-response 2h for P1
ratio   = elapsed / target

state:
  ratio < 0.75 → green
  0.75 ≤ ratio < 1.0 → amber
  ratio ≥ 1.0 → red (breached)
```

Recompute every 15 min.

### 8b. DM cadence

| Transition | Action |
|---|---|
| green → amber | DM SE once: *"TIC-091 amber — first response due in 30min"* |
| amber → red | DM SE once: *"TIC-091 breached first-response SLA. Update or escalate."* |
| red → red (still breached, next stage) | DM only on stage change (e.g. first-response red → resolution-target amber/red) |

Never DM more than once per state per ticket per stage.

### 8c. Pause conditions

SLA timers pause when status is:
- `Awaiting customer confirmation` (we're waiting on them)
- A future "Customer-side blocked" sub-state (planned; treat the same)

Timers resume on status change back to `In progress`.

---

## 9. Customer comms — draft templates

Bot drafts, SE/CSM sends. All drafts DM'd to SE first.

### 9a. Initial acknowledgement (in customer channel thread)

```
Hi [customer first name],

Thanks for flagging — we've logged this on our side as a [Bug/Config/FAQ]
and I'll [investigate/get back to you with options/share the relevant doc] shortly.

Quick context if helpful: [bot drafts from description].

I'll keep this thread updated.
```

### 9b. Status update (cadence-driven)

```
Quick update on [TIC-091]:

[free-text drafted from latest internal note; if no note, default to:
"Still investigating — will have more for you by [next SLA checkpoint]."]
```

### 9c. Resolution / awaiting confirmation

```
[Bug:]
We've shipped a fix for this. Could you confirm whether you're still seeing
the issue? If you're no longer hitting it, we'll close this out.

[Config:]
Setup is complete on our side. Let us know if this matches what you needed,
or if there's anything to adjust.

[Hotfix:]
We've applied a workaround that should unblock you. We're still working on a
permanent fix — I'll let you know when that ships.
```

### 9d. Nudge for confirmation (at 24h, 72h, 7d)

```
Just checking back on [TIC-091] — are you good to close this out?
If we don't hear back, we'll auto-close on [date].
```

### 9e. Auto-close note

```
Closing [TIC-091] pending response. Reply anytime in the next 30 days and
we'll reopen it.
```

### 9f. Reclassification internal alert (NOT to customer)

```
**Reclassified:** [old type] → [new type]
**Why:** [reason]
**Next step:** [next step]
**Owner:** @[owner]

Customer thread: [link]
```

---

## 10. Notion API contracts

Bot writes to these databases. All writes include audit metadata (`changed_by`, `changed_at`).

### 10a. Tickets DB

**Create:**
```http
POST /v1/pages
{
  parent: { database_id: TICKETS_DB_ID },
  properties: {
    Title: { title: [{ text: { content: summary }}] },
    Type: { select: { name: type }},
    Subtype: { select: { name: subtype }},
    Status: { select: { name: "New" }},
    Lane: { select: { name: "SE Action" }},     # bugs only
    Priority: { select: { name: suggested_prio }},
    Severity: { select: { name: severity }},
    "Affected orgs": { relation: [{ id: org_page_id }]},
    Reporter: { people: [{ id: reporter_id }]},
    Source: { select: { name: source }},
    "Original Slack link": { url: slack_link },
    Description: { rich_text: [{ text: { content: description }}]}
  }
}
```

**Update status, prio, lane** — single field patches via `PATCH /v1/pages/{id}`. Always paired with an event-log row.

### 10b. Event logs

All event DBs follow the same shape:
```
{
  Ticket: relation → Tickets,
  From: text,
  To: text,
  By: person,
  At: created_time (auto),
  Note: text
}
```

Append-only. Never edit or delete event rows.

### 10c. Orgs DB

Read-only from bot's POV (synced from Userled product DB via Airbyte or equivalent — TBD in DB-setup worktree). Bot reads `customer_weight` formula output.

### 10d. Articles DB

Bot writes new article tasks when SE clicks `Needs article` on an FAQ ticket. State defaults to `Suggested`. Linked to source FAQ ticket via relation.

---

## 11. Autonomy boundary (single reference)

Repeated from the flow doc for build clarity:

| Step | Bot acts | Bot suggests | SE only |
|---|---|---|---|
| Detect `log`/`check` in customer channel | ✓ | | |
| Open pre-filled form | ✓ | | |
| Validate form + create ticket | ✓ | | |
| Dedupe match | | ✓ | |
| Suggest priority (from matrix) | | ✓ | |
| Apply prio override | | | ✓ |
| Multi-customer prio bump | | ✓ | |
| Set P0 | | | ✓ (SE or CTO) |
| Move SE Action ↔ Dev Action | | ✓ | |
| Reclassify | | ✓ | |
| Draft reclassification alert | ✓ | | |
| Send reclassification alert | | | ✓ |
| Draft customer reply | ✓ | | |
| Send customer reply | | | ✓ |
| Move to Resolved | | | ✓ |
| Create underlying-bug ticket after hotfix | ✓ | | |
| Auto-close after 7d silence | ✓ | | |
| SLA recompute + DM SE on amber/breach | ✓ | | |
| Weekly digest | ✓ | | |
| Reopen within 30-day window | | ✓ | |

---

## 12. Configuration the bot needs

External config (per-deploy, editable without code changes):

- `ORGS_DB_ID`, `TICKETS_DB_ID`, `ARTICLES_DB_ID`, event-log DB IDs
- `TECH_ASSISTANCE_CHANNEL_ID`
- `SE_USER_ID`, `CTO_USER_ID`
- `SUPPORT_HANDLE` (currently `@support`)
- `CHANNEL_TO_ORG_MAP` — customer Slack channels mapped to org IDs
- `CRITICAL_PATH_FEATURES` — list of features that warrant P0 candidate flagging
- SLA targets per tier (per §5d of flow doc)
- Prio matrix lookup (see `prio-matrix-worksheet.md`)

---

## 13. Out of scope for v1

- Customer-facing messaging (always SE-mediated)
- Multi-SE assignment / on-call rotation
- Customer-keyword early-warning DMs (parked for v2)
- Linear migration
- Customer-visible ticket status

---

## 14. Build checklist

> **Storage update (2026-05-29):** decision #5 in `implementation-plan.md` dropped Notion in favour of a single SQL store; the "Notion integration token + DB share-access" line below is therefore obsolete.

- [x] Slack app scopes: declared in `slack-manifest.yml`. v1 footprint: `app_mentions:read`, `channels:history`, `channels:join`, `channels:read`, `chat:write`, `commands`, `groups:history`, `groups:read`, `im:history`, `im:write`, `mpim:history`, `reactions:read`, `reactions:write`, `usergroups:read`, `users:read`. The user must install / reinstall the app for the new scopes to take effect.
- [x] Slash commands `/log-ticket` and `/board` registered (manifest + Bolt handlers); legacy `/csbot` retained behind `CUSTOMERBOT_LEGACY_COMMANDS_ENABLED`.
- [x] Modal flows for `csm_intake`, `se_bug`, `reclassify`, `add_affected_org`, `set_deadline` — all shipped.
- [x] ~~Notion integration token + DB share-access~~ — **obsolete (decision #5):** all storage is SQL.
- [x] Channel→org mapping populated — `orgs.slack_channel_id` + `channel_org_cache` populated lazily on lookup miss. Seed orgs via the legacy `/csbot org add` admin command (Chunk 2).
- [x] Prio matrix loaded — `application/priority/matrix.py` reads from `CUSTOMERBOT_PRIO_MATRIX_PATH` (YAML) or falls back to hardcoded defaults; sample at `config/prio_matrix.example.yaml`. Weekly in-process reload + monthly DM-reminder review (decision #4).
- [x] Scheduled jobs running:
  - SLA state-machine scan (15 min, Chunk 8)
  - Auto-close awaiting + CSM pre-close nudges (daily, Chunk 8)
  - SE confirmation-nudge §9d (daily, Chunk 11)
  - SE status-update cadence §9b (hourly, Chunk 11)
  - Weekly digest §5d (Mondays 09:00 SE-local, Chunk 13)
  - Bot-state sweeper (1 min, Chunk 3)
  - P0 candidate scan (30 min, Chunk 7)
  - Monthly prio-matrix-review reminder (Chunk 7)
- [x] Webhook endpoint for in-app submissions — `POST /webhooks/in-app-bug`, HMAC-SHA256 via `X-CustomerBot-Timestamp`+`X-CustomerBot-Signature`, ±5-min replay window (Chunk 14).
- [x] Event-log writes wired on every state-changing operation — status changes (intake, lifecycle, auto-close, reopen), priority changes (initial + override), reclassifications, comms (lane handoff, reclassify send, auto-close note).
- [x] DM templates loaded — §9a/§9b/§9c/§9d/§9e in `application/tracking/comms_drafts.py`; §9f in `application/tracking/reclassify.py`. All pure, frozen, snapshot-tested.
- [x] Smoke test: full happy-path coverage through automated integration tests for all four intake paths. Manual end-to-end smoke pending once the bot is deployed and Slack scopes are reinstalled — see `docs/specs/smoke-test.md`.

**Per-chunk delivery notes:**
- **Chunk 5:** customer-channel `log`/`check` detector with internal-member gating, thread-already-linked suppression, channel→org cache, last-5-thread-message pre-fill, `app_mention` `log this` override.
- **Chunks 6 + 7:** dedupe + priority pipeline with override DM, multi-customer bump suggestion (2/3/5 thresholds), P0 candidate scan, monthly matrix-review reminder.
- **Chunk 8:** green/amber/red SLA clocks per stage, awaiting-customer pause, daily auto-close after 7d, CSM pre-close nudges at 24h/72h/7d before close.
- **Chunk 9:** six interactive ticket-card buttons (Move to Dev Action, Resolved, Resolved via hotfix, Reclassify, Reopen with 30-day window, Add affected org) + shared card-refresh helper.
- **Chunk 10:** reclassify modal + draft + Send flow; internal stakeholders resolved from reporter, owner, CSM, @support. Bot never targets customer channels.
- **Chunk 11:** customer-comms draft library (§9a–§9e) + SE §9d nudge job + SE §9b cadence job.
- **Chunk 12:** Needs-article FAQ button + `/board articles` snapshot.
- **Chunk 13:** weekly Monday-09:00 digest + on-demand `/board` ticket snapshot; retired the legacy daily-digest job.
- **Chunk 14:** in-app submission webhook with HMAC verification + dedupe + `#tech-assistance` feed entry.
- **Chunk 15:** retired the legacy `AddManualTicket` / `HandleIncomingMessage` / `SendReminders` modules; legacy `/csbot` subcommands remain behind the flag.
