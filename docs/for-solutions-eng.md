# CustomerBot for Solutions Engineers

This is the working reference for the human who runs the queue — the Solutions
Engineer (SE). It explains what CustomerBot does for you, how a query becomes a
tracked ticket, and every button you'll touch. If you're setting the app up
instead of using it, start with [Getting Started](getting-started.md) and
[Configuration](configuration.md).

---

## What it does, in one breath

CustomerBot lives in your Slack. When a customer-surfaced query needs tracking,
the bot turns it into a structured ticket, posts a **live card** you drive with
buttons, mirrors the ticket to **Linear**, and DMs you a **twice-daily digest**
of everything that still needs your attention. It never messages a customer.

### Two rules everything follows

1. **Bot suggests, you decide.** The bot drafts messages, fills forms, and
   proposes priority changes — you click the button. It never posts to a
   customer, and it never sets P0 on its own.
2. **Append-only event log.** Every state change is written to an `event_*`
   table. That's where the reporting comes from — reclassification rate, SLA
   breach rate, first-response time by tier all fall out of those rows.

---

## 1. How a query becomes a ticket

There are four ways a ticket gets created. All of them land on the same card and
the same lifecycle.

### 1a. The `log` / `check` detector (customer & support threads)

The bot watches messages in channels it's in. When an **internal** teammate (a
member of the configured internal Slack user-group) types the word **`log`** or
**`check`** in a thread, the bot DMs *that person* a button: **"Open ticket
form"**, pre-filled with a draft description drawn from the last few messages in
the thread and the org guessed from the channel.

- **Suppression:** write **`no log`** or **`no check`** and the detector stays
  quiet — handy for "I'll check with the team" false triggers.
- **Already-logged:** if the thread is already attached to a live ticket, the
  detector won't fire again.
- **`@CustomerBot`:** an internal member mentioning the bot in a thread also
  triggers the form (you don't need to type "log this").
- The bot **never posts publicly** in the customer thread — the prompt is a DM
  to you only.

### 1b. `/log` (and `/l`) — the manual form

Run **`/log`** (or the one-keystroke alias **`/l`**) anywhere to open the intake
form. The channel you run it in pre-selects the org and the Source:

- a DM → Source `DM`
- `#userled-support` → Source `tech-assistance`
- the Gleap in-app channel → Source `in-app`
- `#product` → Source `product-channel`
- any customer channel → Source `customer-channel`

### 1c. "Log ticket" message shortcut

On any message, use the **… → Log ticket** shortcut. This opens the form
pre-linked to that thread, with the quoted message dropped into the description.
Best when you're reading a specific message and want to log *that*.

### 1d. In-app bug submissions

Bugs submitted from inside the Userled app arrive two ways. The signed webhook
(`POST /webhooks/in-app-bug`) bypasses the form entirely: it creates a **Bug**
ticket with Source `in-app`, carries the page URL, screenshot and session-replay
links, and posts a read-only feed entry to `#tech-assistance` for visibility.
Gleap reports posted into the **Gleap in-app channel** are logged the normal way
— use **Log ticket** on the Gleap message; the form pre-selects the `in-app`
source and the thread joins the 🎫→✅ status loop (see [§4](#4-support-threads-userled-support--gleap)).

### The intake form

One form for everything (the old CSM/SE split is retired). Fields:

| Field | Notes |
|---|---|
| **Type** | Bug · Configuration · Product change *(FAQ is not offered at intake — reclassify to it later)* |
| **Platform-wide?** | checkbox (Bug only); sets the Bug subtype |
| **Org** | dropdown of known orgs, plus **➕ Create new org…** (see [§13](#13-the-org-roster)) |
| **Source** | pre-filled from the channel; editable |
| **One-line summary** | max 140 chars |
| **Description** | multiline; pre-filled from the thread when available |
| **Is this blocking / urgent?** | Yes / No — this derives Severity |
| **Deadline (if blocking)** | datepicker; dropped unless blocking |
| **Affected user** | email or name in the customer org |
| **Link** | prod link / replay / area in the app |

There is deliberately **no severity picker and no subtype picker** — severity
comes from the blocking radio, subtype from the type + platform-wide checkbox.

If you don't submit a form within **30 minutes**, the draft is silently dropped
— no phantom tickets.

---

## 2. Ticket taxonomy

**Types:** `Bug` · `Config` · `FAQ` · `Feature request` *(shown as "Product
change" in the picker)*.

**Subtypes:**

- Bug → platform-wide · customer-specific
- Config → setup-integration · custom-form · consultative · reporting
- FAQ → existing-article · update-article · needs-article
- Feature request → new-capability · enhancement

**Statuses:** `New → In progress → Awaiting customer → Resolved → Closed`.
New / In progress / Awaiting customer are the *live* statuses. **Resolved is
terminal** — the card retires the moment you resolve it.

**Lanes** (Bugs): `SE Action` (you) · `Dev Action` (the dev on support).

**Severity:** blocking · degraded · cosmetic · unsure.

**Sources:** customer-channel · dm · call · email · in-app · tech-assistance ·
product-channel.

**Priorities:** P0–P4. Note P0 is never assigned automatically — see
[§7](#7-priority).

Every ticket has a display id **`TIC-NNN`** in Slack. *(Heads-up: the mirrored
Linear issue title uses `Bosh-NNN`, not `TIC-NNN`.)*

---

## 3. The ticket card — your control surface

Each ticket posts one card to the SE tickets channel. It's `chat.update`-d in
place on every change, so it's always current. The body shows priority, type /
subtype, status, lane, a link to the original customer thread, severity, source,
reporter, stakeholders (the affected orgs' CSMs), affected orgs, deadline,
description and any reference links (prod / replay / screenshot).

**Primary buttons:**

| Button | What it does |
|---|---|
| **Resolved** | Opens a modal: pick *No code change* or *Code change* (optional PR link). Sets the ticket Resolved, retires the card, posts the resolved reply + swaps 🎫→✅ on every attached support thread, DMs the org's CSM, and marks the Linear issue Done. |
| **Move to Dev Action** / **Return to SE** | Toggles the lane. Move to Dev DMs the dev on support (see [§9](#9-lanes--the-dev-handoff)). |
| **Reclassify** | Change type/subtype with an audited reason (see [§11](#11-reclassification)). |
| **Add affected org** | Attach another customer; may trigger a multi-customer priority bump suggestion. |
| **Drop** | Close the ticket now (with a confirm dialog). Stops the SLA clock, marks Linear Canceled. Reopen within 30 days. |

**Secondary row:**

| Control | What it does |
|---|---|
| **Set P-level** | Dropdown P0–P4 — re-prioritise straight from the card. Mirrors to Linear. *(P0 is available here.)* |
| **Set deadline** | Datepicker. |
| **Reply needed** | Flags the ticket so it's marked in the digest; clear it when handled. |
| **Set stakeholder** | Assign the CSM per affected org — this sticks at the **org** level, so it carries to that org's future tickets. |
| **Mark platform-wide / customer-specific** | Bug-only; flips the Bug subtype. |
| **Needs article** | FAQ-only; spins off an article suggestion (see [§12](#12-articles)). |

**Retired cards** (Resolved / Closed) collapse to a struck-through header, the
affected org, an optional "Resolved via …" line, and a single **Reopen** button.
Reopen within **30 days** returns it to In progress; after that the bot suggests
creating a new linked ticket instead.

---

## 4. Support threads (`#userled-support` & Gleap)

The status loop runs in two channels: **`#userled-support`** and the **Gleap
in-app channel**. When a ticket is attached to a thread in either, the bot adds a
**🎫** reaction while it's in flight, and on resolve posts a short reply and
swaps the reaction to **✅**:

> ✅ This has been marked as *resolved*. If you're still seeing the issue, just
> reply here and we'll take another look.

A ticket can have several attached threads (raised by different people, or merged
in via dedupe) — resolve fans out across all of them.

**Link an existing ticket to a thread:** in `#userled-support`, use the
**… → Link to existing ticket** shortcut instead of logging a duplicate. It
lists your live tickets; picking one attaches the thread (🎫) and confirms.

On the `/board` view, the **company name links to the original customer thread**,
the **TIC-NNN links to the card**, and each line shows the **deadline /
days-remaining** when one is set.

---

## 5. Dedupe — suggest, never auto

On every new ticket, the bot checks live tickets for a likely duplicate:

- **Exact prod-link match** → strongest signal.
- **Same org + similar wording** (token overlap ≥ 0.6 on summary + description).
- **Different org + same severity + same feature area** (overlap ≥ 0.7) → likely
  a multi-customer bug.

If it finds one, it DMs you **"Merge into TIC-NNN"** vs **"Create new"**. It
never merges on its own. Merging appends the new context to the existing ticket,
adds the new org if cross-org, attaches the new thread, and logs an audit event.

---

## 6. What the bot notifies you about

**One notification channel: the twice-daily digest.** The bot DMs you the
**open-tickets digest at 10:00 and 17:00** (your local time). It lists the
tickets that need *your* action — **New + In progress**, sorted P0→P4 then
oldest first — with age, a thread link, counts by tier, and a 💬 marker on
anything you've flagged **Reply needed**. If nothing's open, it stays quiet.

This one digest replaced the old weekly digest and the separate 5pm
reply-needed digest.

**Things the bot deliberately does *not* do:**

- **No per-stage SLA pings.** The SLA machine still runs (see below) but is
  **silent by design** — it never DMs you on amber/red. Open work surfaces in
  the digest instead.
- **No 7-day auto-close.** Tickets don't close themselves on silence; you Drop
  or Resolve them.

---

## 7. Priority

**Customer weight** = ACV × sentiment × renewal. ACV steps small→enterprise
(1.0→4.0); negative sentiment weights up, positive down. When a renewal date is
set, **renewal proximity replaces the status multiplier** — ×1.5 at ≤3 months or
overdue, ×1.25 at ≤6 months. The result buckets to low / medium / high /
critical.

**The matrix** maps (customer weight × severity) → a suggested priority, and is
reloaded from its YAML weekly. **P0 is never produced by the matrix** — any P0 in
the matrix is clamped to P1. P0 only ever arrives via a **Set P0** button (from a
bump/scan suggestion) or the card's **Set P-level** dropdown.

**Suggestions the bot makes (you confirm):**

- **Multi-customer bump** — when a ticket gains orgs: 2 orgs → +1 tier, 3+ → at
  least P1, 5+ on a critical-path feature → suggest P0. DM'd to you only.
- **P0 candidate scan** — every 30 min, if 5+ orgs hit a critical-path feature
  within a 6h window, DMs you **and the CTO** with a Set-P0 button.
- **Monthly matrix review** — on the 1st at 09:00, a reminder to re-check the
  weightings (Acknowledge / Snooze 7d).

Set-P-level and every bump write an audit event and mirror the new priority to
Linear.

---

## 8. SLA (internal, silent)

Soft internal targets per tier drive nothing customer-visible. The state machine
runs every 15 minutes and computes green / amber / red clocks per stage (first
response, status update, resolution), pausing while a ticket is *Awaiting
customer*. State is persisted for reporting — but **no DMs are sent**. Default
targets (hours): P0 0.5/2/8, P1 2/24/48, P2 8/48/120, P3 24/168/240, P4 48/–/–.

---

## 9. Lanes & the dev handoff

Bugs start in **SE Action**. When you conclude a dev change is needed, click
**Move to Dev Action**:

- The Linear issue is opened for dev first (so the DM carries a live link).
- Every current member of the **dev-on-support** user-group gets a DM with the
  handoff: priority, severity, affected orgs, source, a Linear work link, the
  repro/context, and thread/replay/screenshot links.
- The card gets a 🛠️ reaction and a threaded "Moved to Dev Action" note.

**Return to SE** flips it back and DMs the support group that it's back with you.

**Inbound from Linear:** when the dev acts on the mirrored issue, the ticket
follows — dev marks **Done** → ticket is **Resolved** (terminal, same as if you
clicked Resolved; reopen from the card if the customer says it isn't fixed);
**Canceled** → the ticket is dropped; **Started** → In progress; a **comment** →
you get a DM. You and the org's CSM are notified. A Done resolve is logged as a
*code change* when a GitHub PR is linked on the issue, else *no code change*.

---

## 10. Linear mirror

Every ticket is mirrored to a Linear issue (best-effort — a Linear outage never
blocks Slack). The issue carries a **per-type label** (Bug / Config / FAQ /
Product change) and **per-org labels**. Status, priority, and type-label changes
sync outbound; dev-side changes sync inbound (see above). A reconcile job runs
every 10 minutes to catch anything either side missed. Linear issue titles are
`Bosh-NNN · title` — the same ticket you see as `TIC-NNN` in Slack.

---

## 11. Reclassification

**Reclassify** opens a modal: new type (Bug / Config / FAQ / Product change), new
subtype, a *why*, a *next step*, and an *owner*. On submit the bot:

- updates the ticket and writes a reclassification audit event (from/to, reason,
  next step, owner),
- **DMs the internal stakeholders** — the original reporter, the named owner, and
  each affected org's CSM — with the change; if the ticket is in Dev Action, it
  also pings the support channel,
- swaps the Linear type-label on a type change.

The bot never sends this to the customer. A high reclassification rate is a
triage-quality signal in reporting.

---

## 12. Articles

FAQ tickets don't wait on documentation. On a FAQ card, **Needs article** spins
off an **Article** in status *Suggested* (owned by whoever clicked), linked back
to the ticket, and DMs you. The article lives on its own queue — view it with
**`/board articles`**. The FAQ ticket itself closes on its own timeline.

---

## 13. The org roster

Customers live in the `orgs` table: name, Slack (or Microsoft Teams) channel,
CSM owner, and ACV / sentiment / renewal (which feed customer weight). The org's
`csm_user_id` is @-mentioned on the card as the stakeholder and DM'd on terminal
states, reclassifications, and dev touches.

- **Create one on the fly:** pick **➕ Create new org…** in the intake form's
  Org dropdown and fill the revealed fields (the owner you set becomes the CSM).
- **Seed / import:** `scripts/seed_org.py` for one org, `scripts/import_orgs.py`
  for a CSV. An `unknown` catch-all org handles unmapped in-app bugs.

---

## 14. What the bot never does

- Never messages a customer — all customer-facing comms go through you or the CSM.
- Never sets P0 on its own.
- Never merges, links, bumps priority, moves lanes, or reclassifies without you
  clicking.
- Never auto-closes a ticket on silence.

The bot acts unprompted only on internal-only bookkeeping: detecting a
`log`/`check`, opening the form, creating the ticket from your submission,
keeping the card and the Linear mirror in sync, running the silent SLA clocks,
and sending you the digest.

---

## Cadence at a glance

| Job | Runs |
|---|---|
| Draft/pending sweeper | every 1 min (drops 30-min-old drafts, 7-day pending rows) |
| P0-candidate scan | every 30 min |
| SLA state machine (silent) | every 15 min |
| Open-tickets digest | DM'd at 10:00 & 17:00 local |
| Monthly matrix review | 1st of month, 09:00 |
| Linear reconcile | every 10 min *(only if Linear is configured)* |

---

## Where to go next

- [Commands](commands.md) — the exact slash commands and card buttons
- [Slack Integration](integrations/slack.md) — app manifest & scopes
- [Linear Integration](integrations/linear.md) — the mirror setup
- [Configuration](configuration.md) — every `CUSTOMERBOT_*` env var
- `docs/specs/se-ticketing-flow-v1.md` — the original design spec (note: parts of
  §4 and §14 predate the current single-form / SQLite + Linear implementation)
