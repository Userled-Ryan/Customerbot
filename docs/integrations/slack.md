# Slack integration

customerbot ships a Slack app manifest at the repo root
([`slack-manifest.yml`](https://github.com/Userled-Ryan/Customerbot/blob/main/slack-manifest.yml)).
Treat that file as the source of truth; this guide explains how to use
it.

## Step 1: Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create
   New App** → **From an app manifest**.
2. Select your workspace.
3. Paste the contents of `slack-manifest.yml` (or upload it directly).
4. Click **Create**.

The manifest declares the intake commands (`/log` and its `/l` shortcut)
and `/board`, plus the full scope footprint v1 needs.

## Step 2: Install to the workspace

1. **Install App** → **Install to Workspace** → authorise.
2. Copy the **Bot User OAuth Token** (`xoxb-…`) — this is
   `CUSTOMERBOT_SLACK__BOT_TOKEN`.

## Step 3: Get the signing secret

1. **Basic Information** → **App Credentials** → copy **Signing Secret**.
2. This is `CUSTOMERBOT_SLACK__SIGNING_SECRET`.

## Step 4: Set the event / interactivity URLs

The manifest defaults to `https://customerbot-userled.fly.dev/slack/events`. If
you're deploying elsewhere, edit the manifest before creating the app
or update the URLs after creation:

- **Event Subscriptions** → Request URL → `https://YOUR_HOST/slack/events`
- **Interactivity & Shortcuts** → Request URL → `https://YOUR_HOST/slack/events`
- **Slash Commands** → each command's Request URL → `https://YOUR_HOST/slack/events`

Slack sends a verification challenge to the event URL on save — the
bot handles this automatically.

## Step 5: Wire credentials into the bot

```sh
CUSTOMERBOT_SLACK__BOT_TOKEN=xoxb-your-bot-token
CUSTOMERBOT_SLACK__SIGNING_SECRET=your-slack-signing-secret
CUSTOMERBOT_SLACK__WORKSPACE_URL=https://yourcompany.slack.com
```

!!! note "Nested delimiter"
    The double underscore (`__`) in `CUSTOMERBOT_SLACK__*` is the
    nested-config delimiter — it maps to `settings.slack.*` internally.

## Required scopes

All declared in the manifest. Reinstall the app after changing the
list, otherwise new scopes don't take effect.

| Scope | Purpose |
|---|---|
| `app_mentions:read` | Receive `@CustomerBot log this` overrides |
| `channels:history` | Detect `log`/`check` in public customer channels |
| `channels:join` | Join public channels when invited |
| `channels:read` | Resolve channel metadata for org lookups |
| `chat:write` | Post ticket cards, support pings, drafts |
| `commands` | Register `/log`, `/l`, `/board` |
| `groups:history` | Detect `log`/`check` in private customer channels |
| `groups:read` | Resolve private-channel metadata |
| `im:history` | Receive DMs (button click context) |
| `im:write` | DM SE drafts + interactive cards |
| `mpim:history` | Group-DM context |
| `reactions:read` · `reactions:write` | Held for upcoming reaction-based audit signal |
| `usergroups:read` | Look up `@support` rotation membership |
| `users:read` | Resolve user names for cards / drafts |

## Event subscriptions

| Event | Used by |
|---|---|
| `app_mention` | `@UserledSupport log this` override → opens the SE-bug modal |
| `message.channels` · `message.groups` · `message.im` · `message.mpim` | `log`/`check` detector (Chunk 5) |

## Slash commands

See [Commands](../commands.md) for what each does. The manifest
registers:

| Command | Description |
|---|---|
| `/log` | Open the ticket-intake modal |
| `/l` | One-keystroke shortcut for `/log` |
| `/board` | Snapshot live tickets or articles (ephemeral) |

## Inviting the bot to channels

After install, the bot needs to be invited to any channel where you
want it to:

- Detect `log` / `check` in customer threads
- Post ticket cards (`SE_TICKETS_CHANNEL_ID`)
- Post `@support` lane-handoff pings (`SUPPORT_PING_CHANNEL_ID`)
- Drop in-app feed entries (`TECH_ASSISTANCE_CHANNEL_ID`)
- DM the SE the twice-daily open-tickets digest (no channel — sent to `SE_USER_ID`)

In Slack:

```
/invite @UserledSupport
```

## Manifest reference

??? example "slack-manifest.yml"
    ```yaml
    display_information:
      name: UserledSupport
      description: Triages and tracks customer-surfaced queries for Solutions Engineering
      background_color: "#24292f"

    features:
      bot_user:
        display_name: UserledSupport
        always_online: true
      slash_commands:
        - command: /log
          url: https://customerbot-userled.fly.dev/slack/events
          description: Open a ticket-intake form
          usage_hint: " "
          should_escape: false
        - command: /l
          url: https://customerbot-userled.fly.dev/slack/events
          description: Open a ticket-intake form (shortcut for /log)
          usage_hint: " "
          should_escape: false
        - command: /board
          url: https://customerbot-userled.fly.dev/slack/events
          description: Snapshot live tickets or articles (ephemeral)
          usage_hint: "[articles | tickets]"
          should_escape: false

    oauth_config:
      scopes:
        bot:
          - app_mentions:read
          - channels:history
          - channels:join
          - channels:read
          - chat:write
          - commands
          - groups:history
          - groups:read
          - im:history
          - im:write
          - mpim:history
          - reactions:read
          - reactions:write
          - usergroups:read
          - users:read

    settings:
      event_subscriptions:
        request_url: https://customerbot-userled.fly.dev/slack/events
        bot_events:
          - app_mention
          - message.channels
          - message.groups
          - message.im
          - message.mpim
      interactivity:
        is_enabled: true
        request_url: https://customerbot-userled.fly.dev/slack/events
      org_deploy_enabled: false
      socket_mode_enabled: false
      token_rotation_enabled: false
    ```
