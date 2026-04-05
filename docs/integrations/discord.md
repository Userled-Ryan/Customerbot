# Discord

This guide walks you through creating a Discord bot and connecting it to prbot.

## Step 1: Create a Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** and give it a name (e.g. `prbot`)
3. Go to the **Bot** section in the sidebar

## Step 2: Configure the bot

1. Under **Privileged Gateway Intents**, enable:
    - **Message Content Intent** — required for prbot to read message text and detect PR URLs

2. Optionally customise the bot's username and avatar

!!! warning "Message Content Intent"
    Without this intent enabled, the bot will not be able to read message contents and PR detection will not work. This is a privileged intent that must be explicitly enabled.

## Step 3: Get the bot token

1. In the **Bot** section, click **Reset Token**
2. Copy the token — you will only see it once

## Step 4: Invite the bot to your server

1. Go to **OAuth2 > URL Generator**
2. Select the following scopes:
    - `bot`
3. Select the following bot permissions:
    - **Read Messages/View Channels**
    - **Read Message History**
    - **Add Reactions**
4. Copy the generated URL and open it in your browser
5. Select the server you want to add the bot to and click **Authorize**

## Step 5: Configure prbot

Add the bot token to your `.env` file:

```sh
PR_BOT_DISCORD__BOT_TOKEN=your-discord-bot-token
```

!!! note "Nested delimiter"
    The double underscore (`__`) in `PR_BOT_DISCORD__BOT_TOKEN` is the nested config delimiter — it maps to `settings.discord.bot_token` internally.

## Required permissions

| Permission               | Purpose                                |
| ------------------------ | -------------------------------------- |
| **Read Messages**        | See messages in channels               |
| **Read Message History** | Backfill messages posted while offline |
| **Add Reactions**        | React to messages with status emoji    |

## Required intents

| Intent                   | Purpose                                      |
| ------------------------ | -------------------------------------------- |
| **Message Content**      | Read message text to detect PR URLs          |
| **Guilds**               | List guilds and channels the bot is in       |

## How it connects

Unlike Slack (which uses HTTP webhooks), the Discord integration connects via the **Discord Gateway** (WebSocket). When prbot starts:

1. The bot connects to Discord's WebSocket gateway
2. It receives `on_message` events for all channels it can see
3. PR URLs are detected and processed in real time
4. The cursor system tracks the last-seen message per channel for backfill on restart

No public URL or webhook endpoint is needed for Discord — only the bot token.
