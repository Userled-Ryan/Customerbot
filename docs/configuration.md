# Configuration

customerbot is configured via environment variables with the
`CUSTOMERBOT_` prefix. Copy `.env.example` to `.env` to get started:

```sh
cp .env.example .env
```

Keys whose feature gate isn't set fail closed — the bot still boots,
but the affected job/handler logs a warning and skips. This is
intentional: each chunk shipped its own gate so the bot stays runnable
during incremental config rollout.

## Required

These three are the minimum to boot:

| Variable | Description |
|---|---|
| `CUSTOMERBOT_SLACK__BOT_TOKEN` | Slack bot OAuth token (`xoxb-…`) |
| `CUSTOMERBOT_SLACK__SIGNING_SECRET` | Slack app signing secret |
| `CUSTOMERBOT_SE_USER_ID` | Solutions Engineer Slack user ID — recipient of every draft DM |

!!! info "Nested config"
    The double underscore (`__`) is the nested delimiter.
    `CUSTOMERBOT_SLACK__BOT_TOKEN` maps to `settings.slack.bot_token`
    internally.

Legacy alias: `CUSTOMERBOT_RYAN_USER_ID` is still accepted as a
synonym for `SE_USER_ID` for backwards compatibility.

## People

| Variable | Description |
|---|---|
| `CUSTOMERBOT_SE_USER_ID` | Solutions Engineer (required) |
| `CUSTOMERBOT_CTO_USER_ID` | CTO; receives P0-candidate flags and SE-OOO fallback (flow §13) |
| `CUSTOMERBOT_INTERNAL_USER_GROUP_ID` | Slack user-group whose members can trigger the `log`/`check` detector |
| `CUSTOMERBOT_SUPPORT_HANDLE` | `@support` Slack user-group ID — pinged via `<!subteam^…>` on SE→Dev handoff |

## Channels

| Variable | Used by | Notes |
|---|---|---|
| `CUSTOMERBOT_TECH_ASSISTANCE_CHANNEL_ID` | `/log-ticket` (CSM intake) · in-app feed entry | When unset, the in-app webhook still works but no `#tech-assistance` feed entry is posted |
| `CUSTOMERBOT_SE_TICKETS_CHANNEL_ID` | Ticket card · weekly digest | The v1 replacement for the Notion board |
| `CUSTOMERBOT_SUPPORT_PING_CHANNEL_ID` | `Move to Dev Action` handoff | When unset, the lane flips but no `@support` ping is posted |

## Feature configuration

| Variable | Description |
|---|---|
| `CUSTOMERBOT_CRITICAL_PATH_FEATURES` | JSON list of feature names that count as "critical-path" for P0 candidate flagging (flow §5c). e.g. `'["publishing","scheduling","reporting"]'` |
| `CUSTOMERBOT_PRIO_MATRIX_PATH` | Path to `prio_matrix.yaml` (decision #4). Falls back to hardcoded defaults when unset |
| `CUSTOMERBOT_SLA_TARGETS` | JSON dict overriding the §5d defaults per priority tier |
| `CUSTOMERBOT_SE_TIMEZONE` | IANA TZ name — used to schedule Monday-09:00 digest and 1st-of-month matrix-review reminder. Defaults to UTC |

### Default SLA targets

Used if `CUSTOMERBOT_SLA_TARGETS` is unset.

| Priority | First response | Status update | Resolution |
|---|---|---|---|
| P0 | 30 min | 2h | 8h |
| P1 | 2h | 24h | 48h |
| P2 | 8h | 48h | 120h |
| P3 | 24h | 168h (7d) | 240h (10d) |
| P4 | 48h | — (uncommitted) | — (uncommitted) |

## Webhooks

| Variable | Description |
|---|---|
| `CUSTOMERBOT_INAPP_WEBHOOK_SECRET` | HMAC-SHA256 shared secret for `POST /webhooks/in-app-bug`. Generate with `openssl rand -hex 32`. Without it the endpoint returns 503 (fail-closed) |

## Feature flags

| Variable | Default | Description |
|---|---|---|
| `CUSTOMERBOT_LEGACY_COMMANDS_ENABLED` | `false` | Re-enables the legacy `/csbot` subcommands (`keyword`, `timezone`, `reminder`, `alerts`, `settings`, `summary`, `close`, `org add`) and the `app_mention` auto-summary. None of these are part of the v1 ticketing flow; kept for transitional use |

## Server

| Variable | Default | Description |
|---|---|---|
| `CUSTOMERBOT_HOST` | `0.0.0.0` | Server bind address |
| `CUSTOMERBOT_PORT` | `8080` | Server port |
| `CUSTOMERBOT_DATABASE_PATH` | `data/customerbot.db` | SQLite path. SQLAlchemy makes Postgres portable when we outgrow SQLite |

## Example `.env`

```sh
# --- Required ---
CUSTOMERBOT_SLACK__BOT_TOKEN=xoxb-...
CUSTOMERBOT_SLACK__SIGNING_SECRET=...
CUSTOMERBOT_SLACK__WORKSPACE_URL=https://yourcompany.slack.com
CUSTOMERBOT_SE_USER_ID=U0123456789

# --- Channels (recommended for full feature coverage) ---
CUSTOMERBOT_TECH_ASSISTANCE_CHANNEL_ID=C...
CUSTOMERBOT_SE_TICKETS_CHANNEL_ID=C...
CUSTOMERBOT_SUPPORT_PING_CHANNEL_ID=C...

# --- People ---
CUSTOMERBOT_CTO_USER_ID=U0123456789
CUSTOMERBOT_INTERNAL_USER_GROUP_ID=S0123456789
CUSTOMERBOT_SUPPORT_HANDLE=S0123456789

# --- Features ---
CUSTOMERBOT_CRITICAL_PATH_FEATURES=["publishing","scheduling","reporting"]
CUSTOMERBOT_PRIO_MATRIX_PATH=config/prio_matrix.yaml
CUSTOMERBOT_SE_TIMEZONE=Europe/London

# --- In-app webhook ---
CUSTOMERBOT_INAPP_WEBHOOK_SECRET=$(openssl rand -hex 32)

# --- Server ---
CUSTOMERBOT_DATABASE_PATH=data/customerbot.db
```
