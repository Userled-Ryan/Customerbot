# Linear integration (v1.5)

customerbot mirrors **every** ticket into Linear, in parallel with the Slack
flow, for two reasons:

1. **SE queue.** New (SE-lane) tickets are mirrored into a Linear **SE
   Responder** project — the SE's queue, viewable/filterable in Linear rather
   than only in Slack. The ticket's **SE owner** is mirrored onto the issue as
   its assignee, so the SE view can group/filter by owner.
2. **Dev handover.** Tickets moved to the Dev lane move into the Linear
   **Product Responder** project where engineers work them (and back into SE
   Responder on *Return to SE*). When a dev changes the issue, the SE + the
   ticket's stakeholder CSMs are notified, and the change is reflected back onto
   the customerbot ticket.
3. **Reporting.** Tickets the SE resolves directly in Slack are still created
   in Linear and closed — so the CTO / Head of CS get a complete, filterable
   record of all activity.

The SE's Slack flow is unchanged. If Linear is not configured, the bot runs
Slack-only (a no-op gateway), so this whole integration is opt-in.

## What maps where

Each issue is created at **team** level with a **per-org label** (keyed off the
org id, displayed as the org name) so Linear can be filtered by org, and its
**assignee** set to the ticket's SE owner (via the Slack→Linear `USER_MAP`).
Time comes from Linear's native created/completed timestamps; "solved" vs
"in-flight" is the workflow state.

Project membership is **lane-scoped** — an issue belongs to one project at a
time, so a lane change moves it between the two queues:

| customerbot status / lane | Linear state | Project |
|---|---|---|
| New (SE lane) | Triage | **SE Responder** |
| In progress (SE lane) | In Progress | **SE Responder** |
| Moved to Dev lane | In Progress | **Product Responder** |
| Returned to SE lane | (recomputed) | **SE Responder** |
| Awaiting customer | Awaiting Customer (falls back to Done if absent) | unchanged |
| Resolved / auto-closed | Done | — |
| Dropped | Canceled | — |

Inbound (dev changes in Linear → customerbot), dev-lane tickets only:

| Linear change | customerbot effect |
|---|---|
| → In Progress | ticket → In progress |
| → Done | ticket → Awaiting customer + SE resolution draft |
| → Canceled | ticket → Closed |
| comment / other edit | notify only, no status change |

Every inbound case also DMs the SE + the ticket's stakeholder CSMs.

## Step 1: Create the API token

**Settings → API → Personal API keys → Create key** with write access to the
team. This is `CUSTOMERBOT_LINEAR__API_TOKEN`.

**Use a dedicated bot/service user** for this token, not a personal account.
The bot's own writes carry the token's user as the actor; the inbound self-echo
filter drops events from that actor. With a personal token, your own manual
Linear edits would be treated as self-echoes and not sync back — fine for a
demo, wrong for production where you want every human dev change to flow.

There's no workflow-state setup to do: the resolver matches Triage / In
Progress / Done / Canceled by name, and maps "Awaiting customer" to **Done**
when the team has no dedicated state for it (add one named "Awaiting Customer"
and it's picked up automatically).

## Step 2: Configure the bot

Only the token + team id are required — the project ids (Product Responder and
SE Responder, matched by name), the workflow-state ids, and the bot's own actor
id are auto-resolved from the token at startup.

```sh
CUSTOMERBOT_LINEAR__API_TOKEN=lin_api_...
CUSTOMERBOT_LINEAR__TEAM_ID=...            # team that owns both Responder projects
# Optional — auto-resolved if omitted:
# CUSTOMERBOT_LINEAR__PROJECT_ID=...       # Product Responder (dev queue)
# CUSTOMERBOT_LINEAR__SE_PROJECT_ID=...    # SE Responder (SE queue)
# CUSTOMERBOT_LINEAR__ACTOR_ID=...
# CUSTOMERBOT_LINEAR__WORKFLOW_STATES__DONE=<stateId>   (and TRIAGE / IN_PROGRESS / AWAITING_CUSTOMER / CANCELED)
# Slack→Linear user map so the SE owner is mirrored as the issue assignee:
# CUSTOMERBOT_LINEAR__USER_MAP={"U08AL6BAAQN":"<linear-user-uuid>"}
```

For the `userledio` workspace the values are: team `Product`
(`ca636ef1-bcfa-41fd-a980-f7d20a7140c3`, key `PRO`), project Product Responder
(`0761aaf7-b21d-429a-8081-c99588123368`), project SE Responder
(`d893ea30-2cdc-47b2-9379-4879cceca88c`). SE owners map to Linear users as:
Ryan `U08AL6BAAQN` → `1e05fa91-8f1e-4bd7-9f39-73c61e7c4b50`, Elizaveta
`U0BEZCALK0E` → `bb417a37-de77-4dd5-85f4-a7942e9e250e`. The Product team has no dedicated
"Triage" state, so the `WORKFLOW_STATES__*` ids are set explicitly (new tickets
land in **Todo**): TRIAGE=`4bc74b2a-1110-4cac-afc3-bb91dd6dcabd` (Todo),
IN_PROGRESS=`fda2b439-ba3f-4ee0-848f-3dcfd2ab48a2`,
DONE=`6814b56e-838c-436f-b8f7-3e6394f0ec05`,
CANCELED=`a4bd88f4-fc21-443a-8cbc-1a660291ae75`; AWAITING_CUSTOMER is left unset
and falls back to Done. (This team was migrated from `Core`
(`9bbead70-e67a-4a0c-99a7-bb4d99212198`) in 2026-07.)

## Step 3: Create the webhook

After the bot is deployed, in **Settings → API → Webhooks** create a webhook
pointing at `https://YOUR_HOST/webhooks/linear`, subscribed to **Issues** and
**Comments**. Copy the signing secret into `CUSTOMERBOT_LINEAR__WEBHOOK_SECRET`.
Without the secret the inbound endpoint fails closed (503).

The webhook must be scoped to the team issues are created in (**Product** for
`userledio`) — inbound dev changes only fire for subscribed teams. Prefer **All
public teams** so issues still mirrored to the old `Core` team keep syncing
until they drain.

## Reliability model

- **Failure isolation.** Outbound Linear calls run after the authoritative
  SQLite + Slack writes, are wrapped so they can never raise, and use a short
  timeout. A Linear outage never affects the Slack flow.
- **No desync.** A 10-minute reconcile sweep re-mirrors any ticket whose
  outbound create was dropped and pulls any dev-lane Linear state change a
  missed webhook left unreflected.
- **No sync loops.** Inbound transitions never echo back to Linear
  (`sync_to_linear=False`), webhooks triggered by the bot's own actor are
  ignored, and every transition is idempotent.
