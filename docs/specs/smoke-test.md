# CustomerBot v1 — Smoke test plan

> Manual end-to-end checks to run once the bot is deployed and the Slack
> app has been (re-)installed with the v1 manifest. The automated test
> suite (`pytest`) covers every code path; this doc walks the four
> intake routes through a live Slack workspace + Postgres/SQLite store
> to flush out integration-layer regressions the unit tests can't catch.

## Pre-flight

Before running any path:

- [ ] App installed in the workspace with the v1 manifest's scopes (see
      `slack-manifest.yml`). If you added a scope and didn't reinstall,
      Slack will return `missing_scope` errors that look like routing
      bugs.
- [ ] All `CUSTOMERBOT_*` env keys are set on the deployment — the
      ones that gate v1 features fail closed: missing
      `SE_TICKETS_CHANNEL_ID` skips card posting, missing
      `SUPPORT_PING_CHANNEL_ID` skips the lane-handoff ping, missing
      `INAPP_WEBHOOK_SECRET` makes `/webhooks/in-app-bug` return 503.
- [ ] At least one row in `orgs` with a valid `slack_channel_id` and
      `csm_user_id`. Seed via the legacy `/csbot org add` admin command
      (set `CUSTOMERBOT_LEGACY_COMMANDS_ENABLED=true` temporarily if
      not already on).
- [ ] `CUSTOMERBOT_INTERNAL_USER_GROUP_ID` set to the user-group that
      should trigger the `log`/`check` detector.

## Path 1 — Customer-channel trigger

1. In a customer Slack channel (one that maps to an `orgs` row), an
   internal member posts a message that contains the word `log`.
2. Expect: the bot DMs the internal user a card with an **Open ticket
   form** button.
3. Click the button → the SE bug modal opens, pre-filled with the
   thread permalink and a description drafted from the last five
   messages.
4. Submit → expect:
   - Ticket card posted to `SE_TICKETS_CHANNEL_ID`
   - SE DM with the §9a initial-ack draft
   - Priority-override DM if priority differs from matrix default
   - `event_status_changes` row `null → New`

Sanity checks afterwards:
- `/board` (no args) shows the new ticket grouped under `SE Action / New`.
- Clicking **Resolved** on the card moves status → Awaiting customer,
  fires the §9c draft DM, and re-renders the card.

## Path 2 — `#tech-assistance` form

1. In `#tech-assistance`, run `/log` (or its `/l` shortcut).
2. Expect the CSM intake modal (different from the SE form — first
   field is the free-text "What's going on?").
3. Fill all required fields including the **Blocking?** radio. If yes,
   the blocking-impact text becomes required at submission time.
4. Submit → expect:
   - Ticket card to `SE_TICKETS_CHANNEL_ID`
   - SE DM with the §9a draft
   - Priority looked up from `prio_matrix.yaml` based on org weight ×
     severity (severity inferred from blocking radio)

## Path 3 — DM trigger (`@CustomerBot log this`)

1. In a customer channel thread, mention the bot with the trigger
   phrase: `@CustomerBot log this`.
2. The bot DMs the invoker the **Open ticket form** card (same as
   Path 1's button).
3. Submit → same ticket-card + SE DM behaviour as Path 1.

## Path 4 — In-app submission webhook

1. From the sender side, sign a payload (`openssl rand -hex 32` is the
   `INAPP_WEBHOOK_SECRET` you set):

   ```python
   import hashlib, hmac, json, time
   payload = {
       "org_id": "acme",
       "user_id": "U_CUSTOMER",
       "user_email": "user@acme.io",
       "page_url": "https://app.userled.io/campaigns/42",
       "description": "Filter dropdown won't open",
       "screenshot_url": "https://cdn.userled.io/x.png",
       "session_replay_url": "https://replay.userled.io/abc",
   }
   ts = int(time.time())
   body = json.dumps(payload).encode()
   sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
   ```

2. `POST https://customerbot-userled.fly.dev/webhooks/in-app-bug` with:
   - `X-CustomerBot-Timestamp: {ts}`
   - `X-CustomerBot-Signature: {sig}`
   - `Content-Type: application/json`
3. Expect **202** + `{"status": "accepted", "ticket_id": "TIC-xxx"}`.
4. In Slack: a ticket card in `SE_TICKETS_CHANNEL_ID` and a read-only
   feed entry in `#tech-assistance` (§3d).
5. Negative checks:
   - Strip the signature header → 401 `unauthorized` with
     `reason: missing-headers`
   - Replay the same body with a timestamp from yesterday → 401 with
     `reason: stale`
   - Modify one byte of the body, keep the original signature → 401
     with `reason: signature-mismatch`

## Background-job verification

After leaving the bot running for a normal SE day, spot-check:

- **SLA scan** — a ticket sitting over its `first_response_minutes`
  target should fire one SE DM on green→amber and again on amber→red,
  no spam between transitions.
- **Reply-needed digest** — flag a live ticket **Reply needed** from
  its card (badge appears), then log in after 17:00 SE-local: one DM
  rolling up every still-flagged ticket with a thread link. Clearing
  the flag drops it from the next digest; a day with nothing flagged
  produces no DM. (The old timed §9b/§9d customer-draft jobs were
  removed in favour of this manual flag.)
- **Auto-close** — a ticket parked past 7 days in Awaiting customer
  should transition to Closed, append a comms-log "auto-close-note"
  row, refresh the card, and DM SE with the §9e draft.
- **Monday digest** — log in after Monday 09:00 SE-local; one digest
  in `SE_TICKETS_CHANNEL_ID` listing counts by tier, breach rate, and
  oldest open per tier.

## Failure escalation

If anything in the four paths or background jobs misbehaves, capture:
- The bot's structured log around the event (search for the ticket's
  `TIC-NNN` display id).
- The row state in `tickets` + the relevant `event_*` table — the
  append-only log is the audit trail.
- The Slack payload that triggered it (the request body for webhooks,
  the message text + channel id for events).

File issues against the repo with that triple; debugging without all
three is guesswork.
