# Linear integration (v1.5)

customerbot mirrors **every** ticket into Linear, in parallel with the Slack
flow, for two reasons:

1. **Dev handover.** Tickets moved to the Dev lane become open issues in a
   Linear **Product Responder** project where engineers work them. When a dev
   changes the issue, the SE + the ticket's stakeholder CSMs are notified, and
   the change is reflected back onto the customerbot ticket.
2. **Reporting.** Tickets the SE resolves directly in Slack are still created
   in Linear and immediately closed — silently, no dev alert — so the CTO /
   Head of CS get a complete, filterable record of all activity.

The SE's Slack flow is unchanged. If Linear is not configured, the bot runs
Slack-only (a no-op gateway), so this whole integration is opt-in.

## What maps where

Each issue is created at **team** level with a **per-org label** (keyed off the
org id, displayed as the org name) so Linear can be filtered by org. Time comes
from Linear's native created/completed timestamps; "solved" vs "in-flight" is
the workflow state. Dev-lane tickets are added to the Product Responder
**project** — that project is the clean dev queue; the team-level view filtered
by org label + state + date is the reporting surface.

The team's workflow states are configured to mirror customerbot 1:1:

| customerbot status / lane | Linear state | In Product Responder project |
|---|---|---|
| New (SE lane) | Triage | no |
| In progress (SE lane) | In Progress | no |
| Moved to Dev lane | In Progress | **yes** |
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

Only the token + team id are required — the project id, the workflow-state ids,
and the bot's own actor id are auto-resolved from the token at startup.

```sh
CUSTOMERBOT_LINEAR__API_TOKEN=lin_api_...
CUSTOMERBOT_LINEAR__TEAM_ID=...            # team that owns the Product Responder project
# Optional — auto-resolved if omitted:
# CUSTOMERBOT_LINEAR__PROJECT_ID=...
# CUSTOMERBOT_LINEAR__ACTOR_ID=...
# CUSTOMERBOT_LINEAR__WORKFLOW_STATES__DONE=<stateId>   (and TRIAGE / IN_PROGRESS / AWAITING_CUSTOMER / CANCELED)
```

For the `userledio` workspace the resolved values are: team `Core`
(`9bbead70-e67a-4a0c-99a7-bb4d99212198`), project Product Responder
(`0761aaf7-b21d-429a-8081-c99588123368`). If state names don't match the
defaults the resolver looks for, set the `WORKFLOW_STATES__*` ids explicitly.

## Step 3: Create the webhook

After the bot is deployed, in **Settings → API → Webhooks** create a webhook
pointing at `https://YOUR_HOST/webhooks/linear`, subscribed to **Issues** and
**Comments**. Copy the signing secret into `CUSTOMERBOT_LINEAR__WEBHOOK_SECRET`.
Without the secret the inbound endpoint fails closed (503).

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
