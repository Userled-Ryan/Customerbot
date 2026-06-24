# Solutions Eng Ticketing Flow — v1

**Status:** Source of truth. Update in place as the design evolves.
**Last updated:** 2026-05-29
**Owner:** Ryan (Solutions Engineering)

This document supersedes the v0 diagram. It defines how a query enters Slack, gets triaged, routed, worked, and closed — and what CustomerBot must do at each step. Individual subsystems (bot min-spec, prio matrix, form schemas, Notion DB structure) will be sculpted in separate worktrees; this file is the canonical flow they all serve.

---

## 1. Goal

Turn every customer-surfaced query into a tracked, prioritised ticket with clear ownership and visible status, so:
- No customer message goes silently un-followed-up
- Big-ACV customers default to the front of the queue without small-ACV customers being abandoned
- SE time is spent on resolution, not triage admin
- CSMs can self-serve visibility into "who owns my customer's issue and where is it"
- Recurring metadata (resolution time, breach rate, reclass rate, multi-customer bugs) is captured for reporting from day one

---

## 2. Taxonomy

Every ticket is one of three types. Subtype is a label for reporting and SLA tuning.

| Type | Definition | Subtypes |
|---|---|---|
| **Bug** | Platform not behaving as expected; usually requires a code change to patch/fix. | platform-wide · customer-specific |
| **Config** | Setup, integration, or strategic work where the platform is behaving as designed but the customer needs help to get value. | setup/integration · custom-form · consultative · reporting |
| **FAQ** | A question answerable by an existing or to-be-written help-centre article. May evolve into bespoke advice. | existing-article · update-article · needs-article |

Reclassification is allowed at any time by SE or `@support`. See §9.

---

## 3. Intake — how a query becomes a ticket

Four intake paths. All converge on the same ticket lifecycle.

### 3a. Customer channels (passive bot)
- CustomerBot is in customer Slack channels.
- **Bot is silent** to the customer — it never posts publicly.
- It listens for **Solutions Eng confirmation triggers** in messages from internal members. Triggers:
  - The word `log` ("let me log this and investigate")
  - The word `check` ("I'll check on this")
  - Manual override: `@CustomerBot log this`
- On trigger, bot DMs the SE with a pre-filled ticket form (see §4) drawn from the thread context.
- **Anti-phantom rule:** if the SE doesn't submit the form within 30 minutes, the draft is silently dropped. No phantom tickets.

### 3b. `#tech-assistance` (single intake channel for CSMs)
- Form-only. No free-form posts get logged.
- CSMs invoke `/log-ticket` to open the form.
- Form is ultra-light (4 required fields — see §4).

### 3c. DMs
- SE invokes `/log-ticket` in any DM thread.
- Bot pre-fills with `Source: DM`, on-behalf-of = the other party in the DM, original Slack link auto.
- Use case: CSM DMs SE asking for help with a customer that's not in a shared channel.

### 3d. In-app bug submissions
- Bug submissions made inside the Userled app bypass the form.
- Bot ingests org id, user, screenshot, session replay automatically.
- Surfaced in `#tech-assistance` as a feed for visibility.

---

## 4. Forms

Two form variants, both as Slack modals.

### 4a. CSM intake form (`#tech-assistance`)
Triggered by `/log-ticket`. Ultra-light.

| Field | Type | Required |
|---|---|---|
| Description | text area | ✓ |
| Org | dropdown from Userled DB | ✓ |
| Link to campaign / area in prod | url | ✓ |
| Blocking? | Yes / No | ✓ |
| Campaign go-live / deadline | date | optional |
| Reporter | auto (Slack user) | auto |
| Source | auto (`tech-assistance`) | auto |

If `Blocking = Yes`, bot asks one follow-up: *"What's the impact — campaign delayed, customer escalating, internal commitment at risk?"* (single line).

### 4b. SE / bug submission form
Triggered by the customer-channel `log`/`check` detector, or `/log-ticket` invoked by SE elsewhere. Richer.

| Field | Type | Required |
|---|---|---|
| Org | dropdown (forced — solves Square / Cash App naming collisions) | ✓ |
| Reporter | auto (Slack user) | auto |
| Source | dropdown: customer-channel · DM · call · email · in-app · tech-assistance | ✓ |
| Affected user (in customer org) | text (email or name) | optional |
| One-line summary | text, 140 char | ✓ |
| Description | text area; pre-filled from thread | optional |
| Is this blocking? | radio: Yes · No (severity derived — Yes → blocking, No → degraded) | ✓ |
| Deadline | datepicker (when it must be fixed by, if blocking) | optional |
| Screenshot / video | file | optional |
| Session replay link | url | optional |
| Original Slack link | auto | auto |

**Org dropdown is non-negotiable** — prevents reporter-named aliases from creating org-attribution errors.

---

## 5. Priority

### 5a. Tiers

| Tier | Reserved for |
|---|---|
| **P0** | Ultra-critical platform-wide bugs. Bot can suggest; only SE or CTO sets. |
| **P1** | Single customer blocked (any ACV); large-ACV degraded |
| **P2** | Important config; non-blocking bug |
| **P3** | Standard config request; FAQ requiring article work |
| **P4** | Nice-to-have; low impact |

### 5b. Prio matrix (separate worktree)

The bot reads a lookup matrix combining:
- **Customer weight:** ACV × sentiment × renewal status (refreshed weekly)
- **Issue severity:** blocking / degraded / cosmetic / question (derived from the
  intake form's blocking radio; refined via Reclassify)

Final prio = function of both. Aim is to please all customers; large-ACV is the tie-breaker, not the gate.

Matrix is maintained as its own document — see the worktree for `prio-matrix`.

### 5c. Multi-customer bumps

When a 2nd+ customer is added to an existing bug (via `Affected orgs` field), bot **suggests** a prio bump:

| Affected customers | Suggestion |
|---|---|
| 2 | +1 tier |
| 3+ | P1 minimum |
| 5+ on critical-path feature | Consider P0 |

Bot only ever suggests. SE confirms.

### 5d. Soft SLAs

Not promised externally. Used internally for board colour-coding and SE DM nudges.

| Tier | First response | Status update cadence | Resolution target |
|---|---|---|---|
| P0 | 30 min | Every 2h | Same day |
| P1 | 2h | Daily | 2 business days |
| P2 | Same day | Every 2 bd | 5 business days |
| P3 | 1 bd | Weekly | 10 business days |
| P4 | 2 bd | On request | Best effort |

**Enforcement:**
- Board colour: green (in SLA) · amber (75% elapsed) · red (breached)
- Bot DMs SE once on amber and once on breach. No spam.
- Weekly digest in internal channel: counts by tier, breach rate, oldest open per tier.
- No customer-visible SLA. Internal calibration only.

---

## 6. Ticket lifecycle

```
┌──────┐    ┌────────────┐    ┌─────────────────────────────┐    ┌─────────┐    ┌────────┐
│ New  │──▶ │ In progress│──▶ │ Awaiting customer confirm.  │──▶ │ Resolved│──▶ │ Closed │
└──────┘    └────────────┘    └─────────────────────────────┘    └─────────┘    └────────┘
                  │                          │
                  │ (customer responds        │ (no reply in 7d)
                  │  "still broken"            │
                  │  with new info)            ▼
                  └──────────────         auto-close with note
```

**State definitions:**
- **New** — created, not yet triaged
- **In progress** — SE or Dev is working it
- **Awaiting customer confirmation** — fix/answer delivered; need customer to confirm acceptable / not reproducible
- **Resolved** — customer confirmed; ticket effectively done
- **Closed** — archived; surfaced in reports but not the live board

**Loopback rules:**
- Customer says *"still broken"* with new info → back to `In progress`. Without new info → SE prompts for repro details before reopening.
- No reply in **7 days** → auto-close with a note ("closed pending customer reply; reopen on request").

**Reopen window:** within **30 days** of close → reopen the original ticket. After 30 days → new ticket, linked to old.

---

## 7. Bug workflow specifics

### 7a. Lanes

One board, two lanes — visible to CSMs.

| Lane | Owner | Purpose |
|---|---|---|
| **SE Action** | Solutions Eng | SE is investigating, reproducing, or fixing (including small SE-authored PRs: CSP, layout, copy, isolated frontend changes) |
| **Dev Action** | `@support` (rotating product responder) | Escalated to product team because the fix needs shared-component, backend, or migration changes |

Tickets always start in `SE Action`. Move to `Dev Action` only after SE investigation concludes a dev change is required.

### 7b. SE → Dev handoff

- Triggered by a button on the ticket.
- Bot pre-fills the handoff payload with: reproduction steps, affected customers, current prio, original Slack link, screenshot / replay.
- `@support` is pinged in the configured Slack channel with a deep-link to the ticket.
- If `@support` pushes back ("this is config, not a bug"), they can reclassify (see §9). The ticket reroutes accordingly.

### 7c. Hotfix vs underlying bug

SE-felt pain: get the customer unblocked. Platform-felt pain: real fix still owed. Both must be tracked.

When SE marks a bug `Resolved via hotfix`:
1. Original ticket continues lifecycle to `Awaiting customer confirmation` → `Closed`.
2. Bot **auto-creates** a linked **Underlying bug** ticket on the `Dev Action` board.
3. Underlying bug inherits: repro steps, affected orgs, original prio (capped at P2 — no longer customer-blocking).
4. Default owner: `@support`.
5. If a new customer hits the same issue before the underlying bug is fixed, the new report dedupes into the underlying bug and re-bumps its prio.

This is how recurring tech debt becomes visible. Reporting on `hotfix → underlying-bug close rate` over time signals platform-fragility hot zones.

---

## 8. Config workflow specifics

Same lifecycle. Subtype determines the deliverable:

| Subtype | Typical deliverable |
|---|---|
| setup/integration | Working integration to connected + optimised state |
| custom-form | Built form with parity to customer's reference |
| consultative | Recommendation delivered (call, doc, or message) |
| reporting | Report drafted / data exported / dashboard built |

**Customer-side blocked state:** if progress depends on the customer (e.g. waiting on their IT for Salesforce admin access), SE moves status to `Awaiting customer confirmation` with a note. Same auto-close-after-7d rule applies; CSM is alerted before auto-close so they can chase.

---

## 9. FAQ workflow & Article board

### 9a. FAQ ticket
- Closes as soon as the customer is answered (whether by linking an existing article, a bespoke reply, or both).
- If a new or updated article is needed, FAQ ticket links to an Article task — but does **not** wait for it to ship.

### 9b. Article board (separate Notion DB)

Owned by SE today; visible to anyone on the team who writes articles.

| State | Meaning |
|---|---|
| Suggested | Raised by an FAQ ticket; not yet triaged |
| Accepted | We agree it should exist or be updated |
| In progress | Someone is writing it |
| Live | Published |
| Needs update | Live but stale — re-enters queue |
| Rejected | Decided not to write |

Throughput on this board is a reporting metric.

---

## 10. Reclassification

Any internal member (SE, `@support`, CSM) can reclassify a ticket at any time.

**On reclassification, the bot drafts a notification** (does **not** auto-send) containing:
- **What changed:** old type → new type
- **Why:** free-text reason
- **Next step:** structured field — concrete action
- **Owner:** named person responsible for the next step

Example draft (LinkedIn sync case):

> **Reclassified:** Bug → Config
> **Why:** LinkedIn sync failure root-caused to customer-side permissions, not platform.
> **Next step:** Customer to update permissions XYZ. SE has disconnected the account; once permissions are set, customer reconnects via Userled.
> **Owner:** @marcus (CSM) to communicate to customer.

SE reviews the draft and sends to the relevant internal stakeholders (CSM owner, original logger, `@support` if involved). Bot never sends to customers.

Reclassifications are logged in the `Reclassifications` event DB for reporting (high rate = triage signal).

---

## 11. Dedupe

**Rule:** Always suggest, never auto.

Dedupe runs on every new ticket creation against **live tickets only** (any status except Closed).

| Match | Action |
|---|---|
| Same org + similar wording | Bot suggests *"This looks like TIC-042. Merge?"* → SE confirms → new context appended to TIC-042, no new ticket. |
| Different org + similar wording | Bot suggests linking as a multi-customer bug → if confirmed, new org added to `Affected orgs` of existing ticket; prio bump may be suggested per §5c. |
| No match | New ticket created. |

After 30-day reopen window expires, dedupe stops considering closed tickets — but links to historical tickets are surfaced as context.

---

## 12. Customer communication

- **Bot never messages the customer.** Ever.
- All customer-facing comms go through SE or CSM.
- Bot drafts and surfaces suggested messages at key moments:
  - Initial acknowledgement when ticket is created from a customer channel
  - ~~Status update at SLA cadence (e.g. P1 → daily)~~ — **removed**: no longer timer-fired (see note below)
  - ~~Nudge for confirmation when ticket sits in `Awaiting customer confirmation` (at 24h, 72h, 7d)~~ — **removed** (see note below)
  - Close-with-note when auto-closing due to silence
- SE or CSM sends the draft (or edits and sends, or ignores).

> **Superseded (implementation):** the two *timed* draft moments above
> (SLA-cadence status update and the 24h/72h/7d confirmation nudge) were
> removed. Instead the SE flags **Reply needed** on the ticket card when a
> ticket is waiting on a reply, and gets a single daily 5pm digest of
> everything still flagged. Auto-detection of replies was deliberately not
> built (a last-author heuristic mislabels cases like a CSM posting "raised
> this internally"), so the SE clearing the flag is the authoritative
> "handled" signal. The initial-ack and close-with-note drafts are unchanged.

---

## 13. Coverage

| Scenario | Owner |
|---|---|
| Standard | SE (Ryan) |
| SE OOO, P0 or P1 incoming | Tristan (CTO) |
| SE OOO, P2–P4 | Queue; surface on return |

V2 will introduce multi-SE assignment, on-call rotation, and customer-keyword early-warning DMs to SE/CSM. Not in v1.

---

## 14. Data model (Notion DBs, v1)

Storage is Notion for v1; migrate to Linear if/when the bot is bottlenecked by Notion's API limits or relational complexity.

### Core DBs

**Tickets**
| Field | Type |
|---|---|
| ID | auto (TIC-001…) |
| Title | text |
| Type | select (Bug · Config · FAQ) |
| Subtype | select (per §2) |
| Status | select (per §6) |
| Lane | select (SE Action · Dev Action) — bugs only |
| Priority | select (P0–P4) |
| Severity | select (blocking · degraded · cosmetic · question) |
| Affected orgs | relation → Orgs (many-to-many) |
| Reporter | person |
| Assigned to | person |
| Source | select |
| Original Slack link | url |
| Created at | auto |
| First response at | date |
| Resolved at | date |
| Closed at | date |
| SLA target | formula (from Priority) |
| SLA state | formula (green · amber · red) |
| Linked tickets | relation → Tickets (hotfix-of · dupe-of · article-for) |

**Orgs**
| Field | Type |
|---|---|
| Org ID | text (primary, from Userled DB) |
| Org name | text |
| ACV tier | select |
| Sentiment | select |
| Renewal date | date |
| Renewal status | select |
| Customer weight | formula |
| CSM owner | person |

**Articles**
| Field | Type |
|---|---|
| Title | text |
| Status | select (per §9b) |
| Owner | person |
| URL | url |
| Linked FAQ tickets | relation → Tickets |
| Created at | auto |
| Published at | date |

### Event-log DBs (immutable history for reporting)

- **Status changes** — ticket · from · to · by · at · note
- **Prio changes** — ticket · from · to · by · at · reason
- **Reclassifications** — ticket · from-type · to-type · by · at · reason · next-step · owner
- **Comms log** — ticket · direction (in/out) · channel · sender · message-link · at

These rows are append-only. Notion's "last edited" doesn't preserve history; without these logs, reports like *"average time in In progress for P1 bugs"* or *"reclassification rate"* are impossible.

---

## 15. Reporting (what the metadata unlocks)

From day one, the data model should support:

- Tickets resolved per period, by type / prio / lane
- First-response time distribution by tier
- Time-in-status histogram
- SLA breach rate by tier
- Most-affected orgs (ticket count, escalation count)
- Reclassification rate (signal of triage quality)
- Hotfix → underlying-bug close rate (tech-debt signal)
- Article board throughput
- Multi-customer bug frequency

---

## 16. CustomerBot autonomy boundary

| Step | Bot acts | Bot suggests, SE confirms | SE only |
|---|---|---|---|
| Detect candidate message (`log`/`check`) | ✓ | | |
| Open pre-filled form | ✓ | | |
| Create ticket from submitted form | ✓ | | |
| Assign priority | | ✓ | |
| Dedupe / link | | ✓ | |
| Suggest multi-customer prio bump | | ✓ | |
| Move ticket between lanes | | ✓ | |
| Reclassify type | | ✓ | |
| Draft customer reply | | ✓ | |
| Send customer reply | | | ✓ |
| Mark Resolved | | | ✓ |
| Auto-close after 7d silence | ✓ | | |
| Draft reclassification alert to internal stakeholders | ✓ | | |
| Send reclassification alert | | | ✓ |
| Create linked underlying-bug after hotfix | ✓ | | |
| Set P0 | | | ✓ (SE or CTO) |
| Nudge SE on SLA amber / breach | ✓ | | |

**Rule of thumb:** the bot acts on internal-only state changes and drafting. It always confirms with SE on anything that changes prio, links tickets, or surfaces to a customer.

---

## 17. Parked for v2

- Customer-keyword early-warning DMs (bot DMs SE/CSM when customer uses "urgent" / "stuck" / "broken" without an internal `log`/`check` follow-up)
- Multi-SE assignment, round-robin, on-call rotation
- Migrate from Notion to Linear if Notion becomes a bottleneck
- Customer-visible ticket status (today the ticket is internal only)
- Granular sub-deliverable tracking inside Config tickets (today the deliverable is a single field)

---

## 18. Open questions / future iteration

Things deliberately not nailed down in v1:

1. **Prio matrix values** — the matrix itself is being built in a separate worktree. The lookup behaviour is locked here; the numbers are not.
2. **Channel name** — `#tech-assistance` is the working name. Rename without changing flow.
3. **`@support` rotation mechanics** — who's on rotation, how it changes, how the bot knows.
4. **Notion DB views and filters** — to be set up when DBs are created.
5. **First-week calibration** — SLA targets in §5d are starting points. Revisit after 4 weeks of real data.

---

## Sub-workstreams (separate worktrees)

This document is the shared backbone. The following pieces are built and maintained separately:

| Worktree | Owner | Output |
|---|---|---|
| Prio matrix | SE | Populated ACV × sentiment × renewal lookup; weekly refresh process |
| CustomerBot min-spec | SE | Bot build requirements: triggers, form payloads, Notion API contracts, draft templates |
| Notion DB setup | SE | Live DBs with relations, formulas, views |
| `#tech-assistance` rollout | SE + CSM lead | Channel created, form deployed, CSM training |
| Reporting dashboards | SE | Initial dashboards over the event-log DBs |

Changes to any of those that affect the flow get reflected back here.
