# Configuration

prbot is configured via environment variables with the `PR_BOT_` prefix. Copy `.env.example` to `.env` to get started:

```sh
cp .env.example .env
```

## Environment variables

### Required

| Variable                         | Description                          |
| -------------------------------- | ------------------------------------ |
| `PR_BOT_GITHUB_APP_ID`          | GitHub App ID                        |
| `PR_BOT_GITHUB_PRIVATE_KEY`     | GitHub App private key (PEM format)  |
| `PR_BOT_GITHUB_WEBHOOK_SECRET`  | Secret for verifying GitHub webhooks |

### Slack (optional)

Setting these enables the Slack integration. If omitted, prbot starts without Slack support.

| Variable                         | Description                         |
| -------------------------------- | ----------------------------------- |
| `PR_BOT_SLACK__BOT_TOKEN`       | Slack bot OAuth token (`xoxb-...`)  |
| `PR_BOT_SLACK__SIGNING_SECRET`  | Slack app signing secret            |

### Discord (optional)

Setting this enables the Discord integration. If omitted, prbot starts without Discord support.

| Variable                         | Description                         |
| -------------------------------- | ----------------------------------- |
| `PR_BOT_DISCORD__BOT_TOKEN`     | Discord bot token                   |

!!! info "Nested config"
    The double underscore (`__`) is the nested delimiter. `PR_BOT_SLACK__BOT_TOKEN` maps to `settings.slack.bot_token` and `PR_BOT_DISCORD__BOT_TOKEN` maps to `settings.discord.bot_token` internally.

### Server

| Variable              | Default          | Description                |
| --------------------- | ---------------- | -------------------------- |
| `PR_BOT_HOST`         | `0.0.0.0`       | Server bind address        |
| `PR_BOT_PORT`         | `8080`           | Server port                |
| `PR_BOT_DATABASE_PATH`| `data/pr_bot.db` | Path to SQLite database    |

## Custom emoji

Override the default emoji reactions by setting environment variables with the `PR_BOT_EMOJI__` prefix. Values should be emoji names **without** colons.

| Variable                             | Default                    |
| ------------------------------------ | -------------------------- |
| `PR_BOT_EMOJI__OPEN`                | `eyes`                     |
| `PR_BOT_EMOJI__APPROVED`            | `white_check_mark`         |
| `PR_BOT_EMOJI__CHANGES_REQUESTED`   | `arrows_counterclockwise`  |
| `PR_BOT_EMOJI__COMMENTED`           | `speech_balloon`           |
| `PR_BOT_EMOJI__MERGED`              | `tada`                     |
| `PR_BOT_EMOJI__CLOSED`              | `x`                        |

For example, to use a custom `:shipit:` emoji for approved PRs:

```sh
PR_BOT_EMOJI__APPROVED=shipit
```

This works with custom emoji in your messaging platform — just use the emoji name as it appears in Slack or Discord.

## Example `.env` file

```sh
# GitHub App (required)
PR_BOT_GITHUB_APP_ID=123456
PR_BOT_GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
PR_BOT_GITHUB_WEBHOOK_SECRET=super-secret-value

# Slack integration (optional — omit to disable)
PR_BOT_SLACK__BOT_TOKEN=xoxb-your-bot-token
PR_BOT_SLACK__SIGNING_SECRET=your-slack-signing-secret

# Discord integration (optional — omit to disable)
PR_BOT_DISCORD__BOT_TOKEN=your-discord-bot-token

# Server (optional — defaults shown)
PR_BOT_HOST=0.0.0.0
PR_BOT_PORT=8080
PR_BOT_DATABASE_PATH=data/pr_bot.db

# Custom emoji (optional — defaults shown)
# PR_BOT_EMOJI__MERGED=tada
# PR_BOT_EMOJI__APPROVED=white_check_mark
```
