# Slack

This guide walks you through creating a Slack app and connecting it to prbot.

## Step 1: Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**
2. Choose **From an app manifest**
3. Select your workspace
4. Paste the manifest below (or use the one in `slack-manifest.yml`):

??? example "Slack app manifest"
    ```yaml
    display_information:
      name: PR Bot
      description: Reacts to GitHub PR URLs in Slack with status emoji
      background_color: "#24292f"

    features:
      bot_user:
        display_name: prbot
        always_online: true
      slash_commands:
        - command: /prbot
          url: https://your-domain.com/slack/events
          description: Manage prbot configuration
          usage_hint: "[exclude|include|list-exclusions|config|help]"

    oauth_config:
      scopes:
        bot:
          - channels:history
          - channels:join
          - channels:read
          - commands
          - groups:history
          - groups:read
          - im:history
          - im:write
          - mpim:history
          - reactions:read
          - reactions:write

    settings:
      event_subscriptions:
        request_url: https://your-domain.com/slack/events
        bot_events:
          - message.channels
          - message.groups
          - message.im
          - message.mpim
      org_deploy_enabled: false
      socket_mode_enabled: false
      token_rotation_enabled: false
    ```

5. Click **Create**

## Step 2: Install to workspace

1. In your app settings, go to **Install App**
2. Click **Install to Workspace** and authorise
3. Copy the **Bot User OAuth Token** (`xoxb-...`)

## Step 3: Get the signing secret

1. Go to **Basic Information** in your app settings
2. Under **App Credentials**, copy the **Signing Secret**

## Step 4: Configure prbot

Add both values to your `.env` file:

```sh
PR_BOT_SLACK__BOT_TOKEN=xoxb-your-bot-token
PR_BOT_SLACK__SIGNING_SECRET=your-slack-signing-secret
```

!!! note "Nested delimiter"
    The double underscore (`__`) in `PR_BOT_SLACK__BOT_TOKEN` is the nested config delimiter — it maps to `settings.slack.bot_token` internally.

## Step 5: Configure the event URL

Once prbot is running and publicly accessible:

1. Go to **Event Subscriptions** in your app settings
2. Set the **Request URL** to `https://your-domain.com/slack/events`
3. Slack will send a verification challenge — prbot handles this automatically
4. Save changes

## Required scopes

| Scope              | Purpose                                   |
| ------------------ | ----------------------------------------- |
| `channels:history` | Read messages in public channels          |
| `channels:join`    | Join public channels when invited         |
| `groups:history`   | Read messages in private channels         |
| `im:history`       | Read direct messages                      |
| `im:write`         | Send direct messages                      |
| `mpim:history`     | Read group direct messages                |
| `reactions:read`   | Read emoji reactions                      |
| `commands`         | Register and handle slash commands         |
| `reactions:write`  | Add/remove emoji reactions on messages    |

## Event subscriptions

prbot listens for message events to detect PR URLs:

| Event             | Description                            |
| ----------------- | -------------------------------------- |
| `message.channels`| Messages in public channels            |
| `message.groups`  | Messages in private channels           |
| `message.im`      | Direct messages                        |
| `message.mpim`    | Group direct messages                  |

## Invite the bot

After installation, invite the bot to channels where you want PR tracking:

```
/invite @prbot
```

The bot will automatically detect GitHub PR URLs in messages and add emoji reactions.

## Slash commands

prbot registers a `/prbot` slash command for managing configuration (e.g. excluding users from triggering emoji updates). The command is included in the app manifest above — no additional setup needed.

See [Commands](../commands.md) for the full reference.
