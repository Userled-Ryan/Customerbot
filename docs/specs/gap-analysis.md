# CustomerBot v1 — Gap Analysis

**Generated:** 2026-05-29
**Specs compared against:**
- `docs/specs/se-ticketing-flow-v1.md` (flow doc)
- `docs/specs/customerbot-min-spec.md` (min-spec)

> **Storage decision (2026-05-29, supersedes spec §14):** Notion has been dropped as the v1 storage backend because no Notion workspace was available where the user has integration-creation rights. The implementation will use a single SQL store (SQLite via SQLAlchemy + Alembic, Postgres-portable) for tickets, orgs, articles, event logs, and bot-interaction state. Everywhere this gap analysis says "Notion DB" or "Notion writes," read "SQL table" instead. The application-layer requirements from §14 (taxonomy, lifecycle, event-log shape, etc.) are unchanged — only the persistence layer. See `docs/specs/implementation-plan.md` decision #5 for full reasoning.

**Headline:** the repo is a single-user Slack "thread tracker" persisted in SQLite. The spec is a multi-customer SE ticketing system persisted in Notion with forms, dedupe, prio, SLA, lanes, reclassification, hotfix linkage, and customer-comms drafting. **Almost nothing in the spec is implemented today.** A handful of low-level primitives (Slack send/DM, FastAPI app, scheduled-job scaffolding, channel-name lookup, thread permalink builder) are reusable; the rest of the application + domain + data layers is not aligned and will need to be rebuilt or replaced.

The §-numbered structure below mirrors the specs so we can walk through it together.

---

## A. Already meets spec

The set of things that genuinely match v1 spec is small. Listed here:

### §2a Slack listeners — partial primitive only
- **FastAPI + Bolt event/command wiring exists**, can receive `message`, `app_mention`, and slash commands.
  - `src/customerbot/integration/slack/handler.py:75-85` constructs `AsyncApp(token, signing_secret)` and registers events + commands.
  - `src/customerbot/integration/slack/handler.py:507-512` exposes `POST /slack/events` via `AsyncSlackRequestHandler`.
- This is plumbing the new listeners will sit on top of. The *handlers themselves* don't match spec (see §B / §C).

### §10 / §14 — Slack scopes (partial)
- `chat:write`, `commands` are clearly in use via Bolt; `channels:history` is implicit (we already subscribe to `message`). `users:read` and `im:write` use is hinted at by `conversations_open` in `gateway.py:34`. **Not auditable from code alone** — the actual scopes live in `slack-manifest.yml`, which I have not yet inspected. Flagging for verification in Phase 2.

### §3a "Suppress when message author is the bot itself"
- `handler.py:99` filters `subtype in ("bot_message", "message_changed", "message_deleted")`. This is the bot-author suppression the spec asks for.

### §2c Scheduled jobs (scaffolding only)
- A periodic-job pattern exists: `main.py:108-118` creates `asyncio.create_task(...run_loop(interval_seconds=...))` for two background loops, and `send_daily_digest.py:161-167` shows the `run_loop` shape.
- The *content* of those loops doesn't match spec (existing reminders + digests are not SLA-aware), but the harness is reusable for the SLA recompute / auto-close / weekly-digest jobs.

### §10b Event-log shape — primitive analogue
- `TrackedConversation.reminder_sent_at` (`entities.py:31`) shows the codebase already knows how to track "last action of type X" timestamps. That's a one-line analogue, nothing more — there is no append-only event-log table, and "last edited" semantics in SQLite + Notion are explicitly insufficient per §10b.

### §5d / §8c SLA timer pause-on-status (primitive analogue)
- `TrackedConversation.is_overdue` (`entities.py:37-43`) computes elapsed since `last_ryan_reply_at`, which is structurally similar to "reset SLA clock when SE acts". It does **not** implement the spec's `Awaiting customer confirmation` pause condition, but the shape is close enough to be a starting point.

**That's the full list.** Everything else is either partial or missing.

---

## B. Partially implemented (diverges from spec)

### §2a Slack listeners — wrong triggers
- **Spec:** customer channels listen for `log` / `check` from internal members; `#tech-assistance` reacts to `/log-ticket`; DMs from SE accept `/log-ticket`; in-app webhook posts directly.
- **Code (`handler.py:96-135`):**
  - The `message` handler reacts only when `sender_user_id == ryan_user_id` and the message contains a **configurable keyword** (`handle_incoming_message.py:44-67`). It does not check for the literal tokens `log` / `check`, does not regex on word boundaries, and does not implement the `@CustomerBot log this` manual override.
  - DMs route to `AddManualTicket` and expect a **Slack permalink** in the DM body (`add_manual_ticket.py:14-18`), not a `/log-ticket` invocation.
  - There is no `#tech-assistance`-aware listener.
  - The `app_mention` handler dumps a summary; it doesn't recognise `log this`.
- **Divergence is structural:** the current detector is a single-user "Ryan-only + keyword-match" rule, not the §3a "internal-member + word-boundary `log`/`check`" rule. Will be replaced, not adjusted.

### §2a / §2b Slash commands
- **Spec:** one command, `/log-ticket`, opens a modal form.
- **Code (`handler.py:138-505`):** one command, `/csbot`, with subcommands `summary | close | keyword | timezone | reminder | alerts | settings`. Renders text, not modals. None of these subcommands appear in spec.
- The §13 out-of-scope list does not include any of `keyword|timezone|reminder|alerts|settings` — they are simply *not in v1*. We'll decide per-command in Phase 2 whether to delete, gate, or migrate.

### §6 Ticket lifecycle
- **Spec:** `New → In progress → Awaiting customer confirmation → Resolved → Closed`, with loopback / 7-day auto-close / 30-day reopen window.
- **Code (`value_objects.py:8-11`):** `ConversationStatus = {OPEN, CLOSED}` — two states. No `New / In progress / Awaiting customer confirmation / Resolved` distinction. No 7-day auto-close, no 30-day reopen window. `update_status` (`ports.py:25-27`) exists but is essentially binary.

### §14 Data model — Tickets DB
- **Spec fields not represented at all in `TrackedConversationRow` (`database.py:21-42`):** `Type`, `Subtype`, `Lane`, `Priority`, `Severity`, `Affected orgs` (relation), `Reporter`, `Assigned to`, `Source`, `First response at`, `Resolved at`, `Closed at`, `SLA target`, `SLA state`, `Linked tickets`.
- **Present in code, not in spec:** `reminder_interval_hours`, `reminder_sent_at`, free-text `category` (replaces forced `Type` dropdown).
- The current row's "category" is a free-text label from a keyword match — the spec mandates **forced dropdowns** for Type / Subtype / Severity (§1 principle 3, §4b). Direct contradiction.

### §2c Scheduled jobs — wrong content
- **Spec:** SLA recompute every 15min; SLA amber/breach DM on transition only; auto-close `Awaiting customer confirmation` after 7d; weekly digest Mondays 09:00; prio-matrix refresh weekly.
- **Code:** hourly reminder loop (`send_reminders.py:85-92`) sends a DM whenever a ticket is "overdue" by a configurable per-user-or-per-ticket interval (24h default); daily digest (`send_daily_digest.py`) fires twice (9am & 5pm) in user TZ.
- The daily 5pm digest and twice-daily cadence are not in spec. The "Mondays 09:00" weekly digest doesn't exist. No SLA state machine. No transition-based throttling — the current reminder loop will re-DM as soon as `now - reminder_sent_at ≥ interval` (potentially every interval forever), whereas spec is **once per state transition** (§8b).

### §10b Event log — no append-only history
- **Spec:** every status / prio / reclassification / comms change writes an immutable row to the corresponding event-log DB; reporting depends on this.
- **Code:** mutates `TrackedConversationRow` in place via `update_status` / `update_last_reply` / `update_reminder_sent` (`repository.py` and `ports.py:11-33`). No event-log tables exist. Audit trail is whatever Alembic + SQLite `updated_at` give you — i.e., not enough.

### §12 Customer comms — drafts vs sends
- **Spec:** bot drafts customer-facing replies, DMs the draft to SE, never sends to the customer channel.
- **Code:** `handler.py:130-135` and `handler.py:163-176` post messages directly into channels (via `gateway.send_message`). The bot will today post in any channel it's invited to — directly violating the §1.1 "silent to customers" principle. Mitigation: callers are internal channels in practice, but **the bot has no defensive guard**, so as soon as it's added to a customer channel for the `log`/`check` listener it would post any of the existing summary / close / help messages publicly. See note in §C below.
- One concession: there is `send_ephemeral` (`gateway.py:51-56`), used on `/csbot close`, which is at least not customer-visible. But this is local to the close command and not a general policy.

### §3a Anti-phantom 30-min drop rule (primitive analogue)
- **Spec:** if SE doesn't submit a draft form within 30 min, drop it silently.
- **Code:** there are no draft forms, so technically not "partial". But the closest analogue — `AddManualTicket` — *immediately* creates a row when a DM with a link arrives (`add_manual_ticket.py:78-87`). No "draft / submit" two-phase. Will need to be added when modals are introduced.

### §11 Dedupe (primitive analogue)
- **Spec:** suggest-not-auto dedupe against live tickets by (org, summary-overlap) / (prod_link exact) / (severity + feature + cross-org).
- **Code:** the only existing dedupe is `find_by_thread(channel_id, thread_ts)` in `repository.py` — i.e. "is this exact thread already tracked?". That's not dedupe, it's an upsert guard. The (channel_id, thread_ts) `UniqueConstraint` at `database.py:40` enforces it at the DB level.

---

## C. Missing entirely

This is the bulk of the spec. Grouped by §-section.

### §2 Taxonomy (Bug · Config · FAQ + subtypes)
- No type field, no subtype field, no reclassification mechanism. Free-text `category` (`entities.py:26`) is the closest analogue and doesn't satisfy "forced dropdowns" (§1.3).

### §3 Intake paths
- **§3a customer-channel `log`/`check` detector** — missing (current trigger is keyword-based, see §B).
- **§3a `@CustomerBot log this` manual override** — missing.
- **§3a anti-phantom 30-min drop window** — missing.
- **§3b `#tech-assistance` channel-aware behaviour** — missing entirely; bot has no concept of an intake channel.
- **§3c DM `/log-ticket` (on-behalf-of inference)** — missing; DM flow expects a Slack link.
- **§3d in-app webhook ingest** — missing; no HTTP endpoint other than `POST /slack/events` and `GET /health` (`main.py:135-140`).

### §4 Form payloads
- **§4a CSM intake modal** — missing.
- **§4b SE / bug modal** — missing.
- **§4c Reclassify modal** — missing.
- No `views.open` / modal `view_submission` handlers exist anywhere in the Slack integration. The forced-dropdown principle (Org, Type, Source, Severity) cannot be honoured without these.

### §5 Priority
- **§5a Tiers (P0–P4)** — missing.
- **§5b Prio matrix lookup (customer_weight × severity)** — missing. No prio-matrix file is loaded by `config.py`.
- **§5c Multi-customer prio-bump suggestion** — missing. (No `Affected orgs` concept exists.)
- **§5d Soft SLA targets per tier** — missing.

### §6 Lifecycle
- States `New`, `In progress`, `Awaiting customer confirmation`, `Resolved` — missing (see §B for current 2-state model).
- Loopback rules, 7-day auto-close, 30-day reopen window — missing.

### §7 Bug workflow
- **§7a Lane (SE Action / Dev Action)** — missing. No Lane field, no board.
- **§7b SE → Dev handoff button + `@support` ping** — missing. No `SUPPORT_HANDLE` in config.
- **§7c Hotfix → underlying-bug auto-create** — missing.

### §8 Config workflow
- "Customer-side blocked" sub-state + CSM pre-auto-close alert — missing.

### §9 FAQ workflow & Article board
- Articles DB writes (`§10d`) — missing. No Articles concept anywhere.
- "Needs article" button on FAQ tickets — missing.

### §10 Reclassification
- Bot-drafted reclassification note (old type, new type, reason, next step, owner) — missing.
- Reclassifications event-log DB — missing.

### §11 Dedupe
- Suggest-not-auto dedupe (token overlap, prod_link exact, cross-org severity match) — missing (see §B for the trivial upsert-guard analogue).
- "Merge into TIC-042 / Create new" two-button DM — missing.

### §12 Customer comms — draft templates
- §9a Initial acknowledgement template — missing.
- §9b Status-update template — missing.
- §9c Resolution / hotfix / config templates — missing.
- §9d 24h / 72h / 7d nudge templates — missing.
- §9e Auto-close note template — missing.
- §9f Reclassification internal alert template — missing.

### §13 Coverage
- "SE OOO → Tristan (CTO) for P0/P1" routing — missing. No `CTO_USER_ID` in config.

### §14 Data model — Notion DBs
- **Tickets DB** — missing (no Notion integration at all).
- **Orgs DB** (read-only from Userled product DB) — missing.
- **Articles DB** — missing.
- **Status changes, Prio changes, Reclassifications, Comms log event DBs** — missing.
- The current persistence layer is SQLite. Per §14, "Storage is Notion for v1; migrate to Linear if/when…". So this is a wholesale storage swap, not a sidecar.

### §15 Reporting
- None of the reporting metrics (resolution time per tier, first-response distribution, breach rate, reclass rate, hotfix→underlying-bug rate, etc.) are surfaced. They depend on the event-log DBs that don't exist.

### §16 / §11-of-min-spec Autonomy boundary
- The autonomy table is binding but there's nothing in the code that currently encodes "bot suggests, SE confirms" interactive flows (DM + two-button confirm/skip). No interactive Slack `block_actions` handler exists in `handler.py`.

### §2b Ticket buttons
- `Move to Dev Action`, `Resolved`, `Resolved via hotfix`, `Reclassify`, `Reopen`, `Add affected org` — none exist. No Notion-button or Slack-message-button infrastructure.

### §10 Notion API contracts
- No `notion-client` (or equivalent) dependency in `pyproject.toml` (verified — no Notion references in src or pyproject per grep).
- No DB-ID config (`TICKETS_DB_ID`, `ORGS_DB_ID`, `ARTICLES_DB_ID`, event-log DB IDs) loaded.

### §12 of min-spec — Configuration
None of the spec-required config keys are loaded by `config.py:7-26`. Specifically missing:
- `ORGS_DB_ID`, `TICKETS_DB_ID`, `ARTICLES_DB_ID`, event-log DB IDs
- `TECH_ASSISTANCE_CHANNEL_ID`
- `SE_USER_ID` (current `ryan_user_id` is the closest analogue — same person, different name; trivial rename)
- `CTO_USER_ID`
- `SUPPORT_HANDLE`
- `CHANNEL_TO_ORG_MAP`
- `CRITICAL_PATH_FEATURES`
- SLA targets per tier
- Prio-matrix lookup table

Only `slack.bot_token`, `slack.signing_secret`, `slack.workspace_url`, `ryan_user_id`, `database_path`, `reminder_hours` are loaded today.

### §14 of min-spec — Build checklist (current state)
All ten items unchecked. None of them pass today.

---

## D. Cross-cutting issues / notes for Phase 2

These don't fit cleanly under a single §, but matter for planning:

1. **The whole codebase still identifies as "prbot" in `README.md`.** README is unchanged from a PR-status bot product. Module name is `customerbot`, but README, `pyproject` description, and possibly `slack-manifest.yml` lag. Cosmetic, but worth confirming in Phase 2 whether the user wants a README rewrite as part of the alignment work.

2. **"Ryan" is hard-coded throughout** (`ryan_user_id` param, `_ryan_user_id` attribute, "Ryan" in copy and entity comments). Spec uses `SE_USER_ID` and treats SE as a role; multi-SE is parked for v2 (§13 of flow, §17 v2 list) but the rename from "Ryan" → "SE" is a v1-clean-up.

3. **SQLite → Notion is the biggest single piece of work.** The spec explicitly says Notion is the v1 store (§14 of flow). Decision needed in Phase 2: does SQLite remain as a local cache (rate-limit shield, restart-recovery for in-flight forms) or get deleted entirely? My read of the spec is *Notion is authoritative; bot may keep ephemeral state for 30-min draft-form holding and channel→org caching but tickets live in Notion only*. Will flag explicitly.

4. **Out-of-scope features in the current code that §13 of min-spec explicitly excludes:**
   - **Direct customer-facing posts** (`gateway.send_message` is called from handlers in `handler.py:152-175` etc.) — §13 ("Customer-facing messaging: always SE-mediated") forbids this. Needs gating or deletion.
   - The `app_mention` summary auto-post (`handler.py:127-135`) will post into whatever channel the bot was mentioned in. If a customer ever `@CustomerBot`s, this leaks internal ticket data. Needs gating.
   - `keyword` / `timezone` / `reminder` / `alerts` / `settings` subcommands are not in v1 spec and likely don't survive. To be confirmed.

5. **Tests are sparse.** `tests/application/tracking/__init__.py` and `tests/integration/slack/__init__.py` exist but contain no actual test modules. `tests/test_smoke.py` is the only top-level test. Phase 3 work will largely be writing tests fresh.

6. **Migrations 0002–0005** add keyword tracking, keyword categories, user settings, and ticket-number repacking. All four are features the spec doesn't ask for. They will probably be superseded rather than deleted (Alembic forward-only), but the *tables* themselves may become dead.

---

## E. Summary at a glance

| Spec area | Status |
|---|---|
| §2 Taxonomy (Bug/Config/FAQ + subtypes) | ❌ Missing |
| §3a Customer-channel `log`/`check` detector | ❌ Wrong trigger (keyword-based today) |
| §3a 30-min draft drop rule | ❌ Missing |
| §3b `#tech-assistance` `/log-ticket` | ❌ Missing |
| §3c DM `/log-ticket` | ⚠️ Different shape (Slack-link DM, not slash command) |
| §3d In-app webhook | ❌ Missing |
| §4 Modal forms (CSM / SE / Reclassify) | ❌ Missing |
| §5 Priority (P0–P4, matrix, multi-customer bumps) | ❌ Missing |
| §5d / §8 SLA tracking + DMs | ❌ Missing (only flat-interval reminders today) |
| §6 5-state lifecycle + loopback + 30d reopen | ❌ Missing (binary open/closed only) |
| §7 Lanes / handoff / hotfix linkage | ❌ Missing |
| §9 FAQ + Article board | ❌ Missing |
| §10 Reclassification draft + event log | ❌ Missing |
| §11 Suggest-not-auto dedupe | ❌ Missing |
| §12 Customer-comms draft templates | ❌ Missing |
| §13 Coverage (CTO fallback for P0/P1) | ❌ Missing |
| §14 Notion DBs (Tickets/Orgs/Articles/event logs) | ❌ Missing (SQLite today) |
| §15 Reporting metrics | ❌ Missing |
| §16 Autonomy boundary (interactive confirm/skip) | ❌ Missing |
| §2c Scheduled-job harness | ✅ Reusable scaffolding |
| Slack event/command/modal plumbing | ✅ Bolt + FastAPI in place |
| Per-channel name cache + thread permalink builder | ✅ Reusable |
| DM + ephemeral send | ✅ Reusable |

**End of Phase 1.** Awaiting your review before drafting the Phase 2 plan.
