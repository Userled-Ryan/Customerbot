# CustomerBot v1 — Implementation Plan (Phase 2)

**Generated:** 2026-05-29
**Builds on:** `docs/specs/gap-analysis.md`
**Approved decisions:**
1. ~~Notion is the source of truth for tickets/orgs/articles/event-logs.~~ **Superseded by decision #5.**
2. Existing `/csbot` subcommands (`keyword`, `timezone`, `reminder`, `alerts`, `settings`) and the `app_mention` auto-summary are kept behind a `LEGACY_COMMANDS_ENABLED` feature flag, default **off**.
3. README rewrite + "Ryan" → "SE" rename is deferred. Internally we'll add `SE_USER_ID` as a config-key alias for `ryan_user_id`, but won't churn the codebase on the rename.
4. Prio matrix lives in `config/prio_matrix.yaml`, structure `customer_weight: { severity: tier }`. Bot reloads weekly on the in-process refresh. Additionally, on the **1st of each month** the bot DMs SE prompting a review of the matrix weightings; SE can ack or snooze.
5. **(2026-05-29) Dropping Notion entirely.** Reason: no Notion workspace available where Ryan has integration-creation rights (see the "No available workspaces" blocker on the integration-creation flow). Replacement: a single SQL store for **everything** — tickets, orgs, articles, event logs, and bot-interaction state. Spec §14 explicitly anticipates a storage swap; this brings it forward to v1. Tradeoffs: no Notion board view, so the "live board" surface becomes a continuously-updated Slack message ("ticket card") in an internal channel — see Chunk 4. Long-term we can swap SQL → Linear or a custom UI without re-doing the application layer.

---

## Storage — single SQL database

Everything in one Postgres-ready SQLite database (we already use SQLAlchemy + Alembic, so portability to Postgres is free when needed).

### Ticket-data tables (authoritative)
| Table | Purpose |
|---|---|
| `tickets` | One row per ticket; columns mirror §14 of flow doc (Type, Subtype, Status, Lane, Priority, Severity, Reporter, Source, Original Slack link, etc.) |
| `orgs` | Customer orgs; seeded manually for v1 (no Userled-product-DB sync yet) |
| `ticket_orgs` | Many-to-many: tickets ↔ affected orgs |
| `ticket_links` | Many-to-many: tickets ↔ related tickets, with relation type (`hotfix-of`, `dupe-of`, `article-for`) |
| `articles` | FAQ article tracking (state per §9b of flow doc) |
| `ticket_articles` | FAQ tickets ↔ articles |

### Event-log tables (append-only, never UPDATE / DELETE)
| Table | Records |
|---|---|
| `event_status_changes` | ticket · from · to · by · at · note |
| `event_prio_changes` | ticket · from · to · by · at · reason |
| `event_reclassifications` | ticket · from-type · to-type · by · at · reason · next-step · owner |
| `event_comms_log` | ticket · direction · channel · sender · message-link · at |

A repository-level guard rejects any UPDATE / DELETE on these tables; the application layer can only INSERT.

### Bot-interaction state tables (ephemeral / cache)
| Table | Purpose |
|---|---|
| `draft_form_sessions` | Pending modals; 30-min expiry sweeper drops unsubmitted (§3a anti-phantom) |
| `channel_org_cache` | channel_id → org_id, refreshed from `orgs` table on lookup miss (built so we can later switch to a Userled-product-DB sync without touching call sites) |
| `sla_dm_state` | (ticket_id, stage) → state already DM'd, for "once per state per stage" throttling (§8b) |
| `pending_dedupe_choices` | Outstanding "Merge / Create new" DMs awaiting SE click (§11) |
| `pending_prio_overrides` | Outstanding prio-override DMs awaiting SE click |
| `pending_reclassify_sends` | Drafted reclassification alerts awaiting SE "Send" click (§10) |
| `prio_matrix_review_state` | Last monthly-reminder ack/snooze timestamp (decision #4) |

### Retained-but-legacy
`tracked_conversations`, `tracked_keywords`, `user_settings`, `channel_cursors` — kept; only read/written when `LEGACY_COMMANDS_ENABLED=true`.

---

## "Board" surface

Without a Notion board, SE/CSM visibility ( §1, §15 ) becomes a Slack-native pattern:

- Every new ticket gets a **ticket card** posted by the bot in a configured internal channel (`SE_TICKETS_CHANNEL_ID`).
- The card carries the ticket state, prio, lane, affected orgs, link to the original thread, and the §2b interactive buttons (`Move to Dev Action`, `Resolved`, `Resolved via hotfix`, `Reclassify`, `Reopen`, `Add affected org`).
- On every state change, the bot **updates the same message** (`chat.update`) so the card is always the live view.
- `#tech-assistance` receives the in-app-bug "feed entry" cards (§3d) — a separate, read-only summary surface for CSMs.
- A `/board` slash command (TBD whether to use this name or `/csbot board`) renders an on-demand snapshot grouped by lane × status for users who don't follow `#se-tickets`.

This is the de-facto board for v1 and replaces what would have been Notion's database view.

---

## Ordered chunks (PR-sized)

Each chunk is intended to be a single reviewable PR, ordered so each one is independently testable and unlocks the next. Within each chunk, **every state-change site also writes its event-log row** — that discipline is not a separate chunk.

### Chunk 1 — Config scaffolding + legacy-command flag
**Why first:** every later chunk reads from these keys.

- Add the v1 config keys to `src/customerbot/config.py`:
  - `tech_assistance_channel_id`
  - `se_tickets_channel_id` (new — see "Board surface" above)
  - `se_user_id` (aliases `ryan_user_id`; both accepted, `se_user_id` preferred)
  - `cto_user_id`
  - `support_handle` (Slack user-group ID, see ambiguity #6)
  - `support_ping_channel_id` (where bot pings `@support` on SE→Dev handoff)
  - `internal_user_group_id` (see ambiguity #2)
  - `critical_path_features: list[str]`
  - `sla_targets` — structured per §5d, codable as default
  - `prio_matrix_path` — path to a YAML file with the lookup (decision #4)
  - `inapp_webhook_secret` (HMAC, ambiguity #3)
  - `legacy_commands_enabled: bool = False`
- All new keys optional (`None` permitted) so the bot still boots before the user has filled them in; chunks that depend on a key fail closed with a clear log message.
- Gate everything in `handler.py` that handles `/csbot` and `app_mention` behind `legacy_commands_enabled`.

**Files touched:** `config.py`, `main.py`, `integration/slack/handler.py`, `.env.example` (new keys, no values).

**Tests:** config loader unit test confirms aliases and defaults.

---

### Chunk 2 — Ticket-data SQL schema + DAOs
**Why next:** unblocks every ticket read/write.

- New domain entities under `src/customerbot/domain/tickets/`:
  - `Ticket`, `Org`, `Article`
  - Value objects: `TicketType` (Bug/Config/FAQ), `TicketSubtype` (per §2), `TicketStatus` (per §6), `Lane` (SE Action / Dev Action), `Priority` (P0–P4), `Severity`, `Source`, `Sentiment`, `ACVTier`, `RenewalStatus`
- New Alembic migrations (`0007_…` through `0014_…`):
  - `0007_tickets` — the tickets table
  - `0008_orgs` — orgs table with `slack_channel_id` column (per ambiguity #1 resolution)
  - `0009_ticket_orgs` — many-to-many
  - `0010_articles` + `0011_ticket_articles`
  - `0012_ticket_links` — for hotfix-of / dupe-of / article-for relations
  - `0013_event_logs` — the four append-only event tables, with DB-level triggers (or repository-level guards) blocking UPDATE/DELETE
- New repositories under `src/customerbot/data/repository/tickets.py`, `orgs.py`, `articles.py`, `event_logs.py` exposing the type-safe CRUD operations the app layer will use: `create_ticket`, `update_status`, `add_org_to_ticket`, `query_live_tickets`, `append_event`, `lookup_org_by_id`, `read_customer_weight` (formula computed in code, not in DB).
- The append-only enforcement on event-log writes is checked in both places: repository raises on any non-INSERT, and the migration installs a SQLite-friendly equivalent (triggers that abort on UPDATE/DELETE).

**Files touched:** new `domain/tickets/`, new `data/repository/`, `data/database.py`, `data/migrations/versions/`.

**Tests:** repository round-trips for each table; append-only guard rejects UPDATE/DELETE; org lookup by slack_channel_id; customer_weight computation from ACV × sentiment × renewal.

---

### Chunk 3 — Bot-interaction state SQL schema + sweeper
**Why next:** unblocks modals (Chunk 4) and dedupe/prio/sla DMs (Chunks 6/7/8).

- New migrations:
  - `0015_draft_form_sessions` — pending modals + 30-min expiry timestamps
  - `0016_channel_org_cache` — channel_id → org_id, last-synced-at
  - `0017_sla_dm_state` — (ticket_id, stage) → state already DM'd
  - `0018_pending_dedupe_choices` / `0019_pending_prio_overrides` / `0020_pending_reclassify_sends`
  - `0021_prio_matrix_review_state` (decision #4)
- Domain entities + repositories for each.
- A background sweeper job: every minute, expire `draft_form_sessions` older than 30 min (§3a); also expire `pending_*` rows older than 7 days (housekeeping).

**Files touched:** `data/migrations/versions/`, `data/database.py`, `data/repository/`, `domain/`, `main.py` (register sweeper).

**Tests:** repository round-trips; sweeper drops drafts at exactly 30 min.

---

### Chunk 4 — `/log-ticket` slash command + modals + ticket card
**Why next:** the central intake mechanism (§3b, §3c) **plus** the board-card surface that every later chunk updates.

- Register `/log-ticket` slash command (Slack manifest update needed).
- `views.open` handlers for `csm_intake` (§4a) and `se_bug` (§4b) modals; selection between them is based on invocation channel (`tech_assistance_channel_id` → CSM, else SE).
- `view_submission` handler:
  1. Validate required fields.
  2. Resolve `org` dropdown → `org_id` via Orgs table.
  3. INSERT ticket row.
  4. INSERT `event_status_changes` row (`null → New`).
  5. **Post the ticket card** to `se_tickets_channel_id` with §2b buttons; store the returned `message_ts` on the ticket row so later state-changes can `chat.update` it.
  6. DM SE the §9a initial-acknowledgement draft.
  7. Post a read-only feed entry in `#tech-assistance` (only for in-app submissions per §3d, but the helper lives here).
- Anti-phantom: `views.open` writes a `draft_form_sessions` row; if not submitted in 30min, sweeper drops it (Chunk 3).
- Slack scopes audit: `commands`, `chat:write`, `users:read`, `im:write`, plus modal scopes implicitly via the Bolt manifest. Will verify in `slack-manifest.yml`.

**Files touched:** `integration/slack/handler.py`, new `integration/slack/modals/` directory, new `integration/slack/ticket_card.py`, `slack-manifest.yml`.

**Tests:** view-submission happy-path; required-field validation; 30-min drop; ticket card posts with correct buttons; channel-routing CSM vs SE modal.

---

### Chunk 5 — Customer-channel `log`/`check` detector
**Why next:** the second intake path (§3a). Reuses Chunk 4's modal infrastructure.

- Replace the `message` event handler in `handler.py`:
  - Match: regex `\b(log|check)\b` case-insensitive, message author is in `INTERNAL_USER_GROUP_ID` (ambiguity #2).
  - Suppress: bot author; messages with `no log` / `no check`; messages in threads already linked to a live ticket (`tickets` table lookup by `original_slack_link`).
  - On match: build the pre-filled SE bug modal payload (channel→org via cache; thread permalink; description drafted from last 5 thread messages) and DM the author an interactive message with a single "Open ticket form" button.
- Manual override: `app_mention` containing `log this` (or `@CustomerBot log this`) → same flow.

**Files touched:** `integration/slack/handler.py`, new `application/tracking/detect_log_check.py` use case.

**Tests:** regex matches `log` / `check` boundaries; `logging` / `checking` do **not** match (word boundary); `no log` suppression; in-thread suppression; bot suppression.

---

### Chunk 6 — Suggest-not-auto dedupe
**Why next:** runs on every ticket creation (Chunks 4 and 5).

- New use case `application/tracking/dedupe.py`:
  - Query live tickets via `query_live_tickets(...)`, filtered by org_id and optionally prod_link.
  - Score against incoming summary/description with a simple token-overlap (Jaccard on lowercased word tokens, no stemming — explicit and inspectable).
  - Apply the three match criteria (§6 of min-spec): org+overlap≥0.6, prod_link exact, severity+feature_tag+overlap≥0.7 cross-org.
  - On candidate: INSERT `pending_dedupe_choices` row, DM SE with "Merge into TIC-042 / Create new" buttons.
- New `block_actions` handler:
  - "Merge" → append context as a comment column on TIC-042; if cross-org, INSERT `ticket_orgs` row; trigger Chunk 7's multi-customer bump check.
  - "Create new" → proceed with the ticket creation flow.
- `feature_tag` is an open question (ambiguity #5) — for v1, treat criterion 3 as "skip if feature_tag absent" and document it.

**Files touched:** new `application/tracking/dedupe.py`, `integration/slack/handler.py` (interactive component handler), `slack-manifest.yml` (interactivity URL).

**Tests:** scoring boundary cases; no-match → straight create; SE clicks Merge → org added + event row written.

---

### Chunk 7 — Priority assignment + multi-customer bump + P0 candidate flag + monthly matrix-review reminder
- Load the prio matrix from `prio_matrix_path` (YAML; format: nested `customer_weight × severity → tier`).
- On ticket creation, look up suggested prio, set `Priority`, INSERT `event_prio_changes` (`null → Pn`, reason `"matrix lookup"`), DM SE the rationale + override buttons (§7a of min-spec).
- New `block_actions` handler for the override buttons: UPDATE prio + INSERT `event_prio_changes` with reason `"manual override"`. Update the ticket card.
- Multi-customer bump check: on `ticket_orgs` INSERT (triggered by dedupe-merge or `Add affected org`), if count crosses threshold, DM SE the bump suggestion (§5c, §7b of min-spec).
- P0 candidate flag: scheduled scan every 30 min for "≥5 orgs hit similar issue in 6h on critical-path feature" → DM SE/CTO a flag (never auto-set).
- **Monthly matrix-review reminder (decision #4):** on the 1st of each month at 09:00 SE-local-time, DM SE: *"Time to review the prio matrix weightings. Open `config/prio_matrix.yaml` and adjust ACV × sentiment × renewal weightings if anything has drifted."* with `[Acknowledged]` / `[Snooze 7d]` buttons. Track last-ack in `prio_matrix_review_state`.

**Files touched:** new `application/tracking/priority.py`, prio-matrix loader, scan job, monthly-reminder job.

**Tests:** matrix lookup; override updates field + event log; multi-customer thresholds 2/3/5; P0 flag fires only on critical-path features; monthly reminder fires once per month, snooze pushes 7d.

---

### Chunk 8 — SLA state machine + DMs + auto-close + pause-on-awaiting
- 15-minute job: for every live ticket, compute `elapsed / target` per current SLA stage; transition green/amber/red; UPSERT `sla_dm_state`; DM SE on green→amber and amber→red transitions only.
- Pause SLA when status is `Awaiting customer confirmation`; resume on transition back to `In progress`.
- Daily job: auto-close `Awaiting customer confirmation` tickets older than 7 days; INSERT `event_comms_log` row + draft the §9e auto-close note (DM to SE).
- CSM pre-auto-close alert at 24h / 72h / 7d before auto-close (§9d) — DM the assigned CSM.

**Files touched:** new `application/tracking/sla.py`, new `application/tracking/auto_close.py`, retire `send_reminders.py` (delete reads, keep file or rename — TBD).

**Tests:** transition fires DM once; second tick at same state doesn't refire; pause/resume; auto-close after 7d.

---

### Chunk 9 — Lifecycle + lanes + interactive ticket-card buttons
- Wire the §2b buttons on the ticket card (already posted in Chunk 4):
  - `Move to Dev Action` → UPDATE Lane; ping `support_handle` in `support_ping_channel_id` with a pre-filled handoff payload (repro steps, affected customers, current prio, original Slack link, screenshot/replay).
  - `Resolved` → status → `Awaiting customer confirmation`; DM SE the §9c draft.
  - `Resolved via hotfix` → same + auto-create a linked "Underlying bug" ticket on Dev Action lane (inheriting fields per §7c), INSERT `ticket_links` (`hotfix-of`).
  - `Reclassify` → opens Chunk 10's reclassify modal.
  - `Reopen` → if within 30 days → set status → `In progress`; if older → DM SE "Create a new linked ticket instead?".
  - `Add affected org` → opens org dropdown; on submission triggers Chunk 7's multi-customer bump check.
- Every button click also `chat.update`s the ticket card with the new state.

**Files touched:** `integration/slack/handler.py`, new `application/tracking/lane_handoff.py`, new `application/tracking/hotfix.py`, `integration/slack/ticket_card.py` (update helpers).

**Tests:** each button writes the expected UPDATE + event row; hotfix auto-creates linked ticket; reopen window enforced; card text reflects new state after click.

---

### Chunk 10 — Reclassification (draft, never auto-send)
- `reclassify` modal (§4c).
- On submission: INSERT `event_reclassifications`; draft the §9f internal alert; INSERT `pending_reclassify_sends`; DM SE with a "Send" button; on Send, post to the configured stakeholders (CSM owner, original logger, `@support` if involved) and INSERT `event_comms_log`.
- Bot **never** sends to customers.

**Files touched:** `integration/slack/modals/reclassify.py`, `application/tracking/reclassify.py`.

**Tests:** draft generated with correct fields; bot never targets customer channels; event row written; "Send" wires up correctly.

---

### Chunk 11 — Customer-comms draft templates + nudge job
- Implement §9a–§9e templates as a draft generator. SE/CSM is always the sender; bot never sends.
- Nudge schedule: `Awaiting customer confirmation` tickets get a §9d nudge DM to SE at 24h / 72h / 7d.
- Status-update cadence (§9b) driven by SLA tier.

**Files touched:** new `application/tracking/comms_drafts.py`, `application/tracking/nudges.py`.

**Tests:** template rendering; cadence fires once per checkpoint.

---

### Chunk 12 — Articles board + FAQ workflow
- `Needs article` button on FAQ tickets → INSERT `articles` row (state = Suggested) + `ticket_articles` link to the FAQ ticket.
- FAQ tickets can close without waiting for the article (per §9 of flow doc).
- `/board articles` (or similar) renders the article-board snapshot to Slack.

**Files touched:** `data/repository/articles.py`, `application/tracking/articles.py`, `integration/slack/handler.py`.

**Tests:** Articles row created with correct link; FAQ ticket closes independently.

---

### Chunk 13 — Weekly digest (Mondays 09:00 internal channel) + on-demand `/board`
- Replace `send_daily_digest.py` (legacy) with a Monday-only digest scheduled job.
- Counts by tier, breach rate, oldest open per tier (§5d).
- Plus on-demand `/board` slash command rendering a per-lane × status snapshot to the invoker (replaces what would have been a Notion view filter).

**Files touched:** new `application/tracking/weekly_digest.py`, new `application/tracking/render_board.py`, retire `send_daily_digest.py`.

**Tests:** weekly digest fires on Mondays at the configured local time; `/board` payload contents.

---

### Chunk 14 — In-app submission webhook
- New endpoint `POST /webhooks/in-app-bug` accepting the §3c JSON payload.
- HMAC signature verification using `INAPP_WEBHOOK_SECRET` (ambiguity #3).
- Creates ticket directly with `Source: in-app`, runs dedupe, posts feed entry in `#tech-assistance`.

**Files touched:** new `integration/webhooks/in_app_bug.py`, `main.py` (mount router).

**Tests:** signature verification; happy path; dedupe path.

---

### Chunk 15 — Cleanup + checklist
- Delete now-unused application code (`add_manual_ticket.py`, `handle_incoming_message.py`, `send_reminders.py`, `send_daily_digest.py`). Legacy slash-command subcommands remain behind the flag.
- Verify `slack-manifest.yml` scopes match §14 of min-spec.
- Tick all §14 build-checklist items in `customerbot-min-spec.md`.
- Smoke test the four happy paths: customer-channel trigger, `#tech-assistance` form, DM trigger, in-app submission.

---

## Config keys you need to provide before each chunk

I will scaffold all loaders in Chunk 1 with `None` defaults. Concrete values needed (grouped by chunk that first reads them):

**Chunk 4 (`/log-ticket` + modals + ticket card):**
- `TECH_ASSISTANCE_CHANNEL_ID`
- `SE_TICKETS_CHANNEL_ID` (new internal channel where the bot will post ticket cards)
- `SE_USER_ID` (current `ryan_user_id` works for now)
- Slack-app interactivity URL set in the manifest (so `view_submission` and `block_actions` reach the bot)

**Chunk 5 (`log`/`check` detector):**
- `INTERNAL_USER_GROUP_ID` (per ambiguity #2 resolution)
- Initial seeded `orgs` table — I'll add an admin command or seed script in Chunk 2 so you can populate it without writing SQL.

**Chunk 7 (priority + matrix):**
- `config/prio_matrix.yaml` content — happy to draft a starter file and have you fill in numbers.
- `CRITICAL_PATH_FEATURES` list — comma-separated env var or YAML.
- Decision on `feature_tag` (ambiguity #5).

**Chunk 8 (SLA):**
- Concrete `sla_targets` per tier — defaulting to the values in §5d unless you say otherwise.
- `CTO_USER_ID` (for P0 candidate flag DMs).

**Chunk 9 (handoff):**
- `SUPPORT_HANDLE` (Slack user-group ID, e.g. `S0123ABCD`)
- `SUPPORT_PING_CHANNEL_ID`

**Chunk 14 (in-app webhook):**
- `INAPP_WEBHOOK_SECRET` (HMAC shared secret you generate, e.g. `openssl rand -hex 32`).

---

## Ambiguities — all resolved (2026-05-29)

User approved all recommendations ("go with all recs").

1. ~~**Channel→org map source.**~~ **Resolved:** `slack_channel_id` column on the SQL `orgs` table. Bot reads on every channel-side lookup with a SQLite cache.
2. ~~**"Internal member" detection for §3a.**~~ **Resolved:** Slack user-group `INTERNAL_USER_GROUP_ID`. Single config key; Slack handles membership.
3. ~~**In-app webhook auth.**~~ **Resolved:** HMAC-SHA256 with `INAPP_WEBHOOK_SECRET` shared secret in the `X-CustomerBot-Signature` header; reject unsigned or stale (>5 min) requests.
4. ~~**Prio matrix file format and location.**~~ **Resolved:** YAML at `config/prio_matrix.yaml`; weekly in-process reload; monthly DM to SE prompting a weightings review (see Chunk 7).
5. ~~**`feature_tag` on tickets.**~~ **Resolved:** `feature` column on the `tickets` table, populated manually by SE post-creation. Dedupe criterion 3 is skipped when `feature` is null.
6. ~~**`@support` resolution.**~~ **Resolved:** Slack user-group ID (`SUPPORT_HANDLE = "S..."`); ping via `<!subteam^S...>` syntax. Slack handles the rotation membership.
7. ~~**Reopen mechanism (§6).**~~ **Resolved:** Within 30 days, `Closed → In progress`. Older than 30 days → bot suggests "Create a new linked ticket instead?".
8. ~~**First-response timestamp.**~~ **Resolved:** Set when status transitions `New → In progress`.
9. ~~**Comms-log writes.**~~ **Resolved:** Bot logs only when SE clicks a bot-rendered "Send" button. No inference of manual SE customer messages.
10. ~~**Subtype list per Type.**~~ **Resolved:** Canonical lists per §2 of flow doc:
    - Bug: `platform-wide` · `customer-specific`
    - Config: `setup-integration` · `custom-form` · `consultative` · `reporting`
    - FAQ: `existing-article` · `update-article` · `needs-article`

---

## §13 of min-spec — out-of-scope items currently present in the code

Per decision #2: all gated behind `LEGACY_COMMANDS_ENABLED`, default off.

| Existing feature | File:lines | Treatment |
|---|---|---|
| `/csbot keyword` subcommand | `handler.py:237-296` | Flag-gated. Stop reading `tracked_keywords` from new code paths. |
| `/csbot timezone` subcommand | `handler.py:298-336` | Flag-gated. |
| `/csbot reminder` subcommand | `handler.py:338-428` | Flag-gated. Superseded by Chunk 8 SLA DMs. |
| `/csbot alerts` subcommand | `handler.py:430-469` | Flag-gated. Superseded by Chunk 8 + Chunk 13. |
| `/csbot settings` subcommand | `handler.py:471-485` | Flag-gated. |
| `/csbot summary` subcommand | `handler.py:151-154` | Flag-gated. (Live view of open tickets is now the ticket-card feed in `#se-tickets` plus `/board`.) |
| `/csbot close` subcommand | `handler.py:156-235` | Flag-gated. Closing is now via the `Resolved`/`Reopen` ticket buttons (Chunk 9). |
| `app_mention` auto-summary | `handler.py:127-135` | Flag-gated. Risk: leaks ticket data if bot is mentioned in a customer channel. |
| `AddManualTicket` (DM-with-Slack-link → ticket) | `application/tracking/add_manual_ticket.py` | Retire after Chunk 4 ships (`/log-ticket` in DM context replaces it). Delete in Chunk 15. |
| `HandleIncomingMessage` (Ryan-keyword detector) | `application/tracking/handle_incoming_message.py` | Retire in Chunk 5. Delete in Chunk 15. |
| `SendReminders` (hourly flat-interval reminders) | `application/tracking/send_reminders.py` | Retire in Chunk 8. |
| `SendDailyDigest` (9am + 5pm digest) | `application/tracking/send_daily_digest.py` | Retire in Chunk 13. |

---

## Risks / things I want to call out

- **`PR-sized` is aspirational.** Chunk 2 (full ticket schema + DAOs + event-log append-only enforcement) and Chunk 9 (six interactive ticket-card buttons + hotfix linkage) are larger than the others. Happy to split mid-chunk if either grows too big once we're in it.
- **Slack modal `private_metadata` size limit (3000 chars).** Pre-filled descriptions drafted from a thread could exceed this on long threads. I'll truncate to ~2000 chars in Chunk 4.
- **Channel→org cache invalidation.** If a customer org row is updated in `orgs`, the bot's SQLite cache could be stale. Refresh on a 1-hour cron + on-demand if a lookup misses.
- **Org seeding for v1.** Without a Userled-product-DB sync, the `orgs` table starts empty. I'll add a `/csbot org add ...` admin command (under the legacy flag) in Chunk 2 so you can seed it without writing SQL; the API surface is small enough that swapping to a real sync later is a one-file change.
- **Reporting (§15).** The event-log tables give us everything we need, but the actual report-rendering surface (Slack messages? a tiny dashboard?) isn't in v1. The data will accumulate from day one and we can build reports on top once the bot is live.

---

**End of Phase 2.** Awaiting your answers on ambiguities #2, #3, #5, #6, #7, #8, #9, #10 + the green light to start Chunk 1.
