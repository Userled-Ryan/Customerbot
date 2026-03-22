# prbot

A Slack bot that watches for GitHub PR URLs in messages and reacts with emoji reflecting the PR's current status. When a PR's status changes (via GitHub webhooks), the bot automatically updates the reaction.

| PR Status         | Emoji                      |
| ----------------- | -------------------------- |
| Open              | :eyes:                     |
| Approved          | :white_check_mark:         |
| Changes requested | :arrows_counterclockwise:  |
| Commented         | :speech_balloon:           |
| Merged            | :tada:                     |
| Closed            | :x:                        |

## How it works

1. A user posts a message containing a GitHub PR URL in Slack
2. The bot detects the URL, fetches the PR status from GitHub, and adds an emoji reaction
3. When the PR is updated (opened, closed, reviewed, etc.), a GitHub webhook notifies the bot
4. The bot updates the emoji on all Slack messages tracking that PR

## Install

### Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just) (optional, for task running)

### Setup

```sh
# Clone the repo
git clone git@github.com:feds01/prbot.git
cd prbot

# Install dependencies and pre-commit hooks
just install
# or without just:
uv sync --dev && uv run pre-commit install
```

### Configuration

Copy the example env file and fill in your credentials:

```sh
cp .env.example .env
```

| Variable                    | Description                          |
| --------------------------- | ------------------------------------ |
| `PR_BOT_SLACK_BOT_TOKEN`   | Slack bot OAuth token (`xoxb-...`)   |
| `PR_BOT_SLACK_SIGNING_SECRET` | Slack app signing secret          |
| `PR_BOT_GITHUB_TOKEN`      | GitHub personal access token         |
| `PR_BOT_GITHUB_WEBHOOK_SECRET` | Secret for GitHub webhook HMAC   |
| `PR_BOT_DATABASE_PATH`     | SQLite database path (default: `data/pr_bot.db`) |

### Running locally

```sh
just dev
# or:
uv run uvicorn prbot.main:api --reload
```

The server starts at `http://localhost:8080` with endpoints:

- `POST /slack/events` — Slack event subscription
- `POST /github/webhooks` — GitHub webhook receiver
- `GET /health` — health check

### Running checks

```sh
just check       # lint + format-check + typecheck
just test        # run tests
just lint-fix    # auto-fix lint issues
just format      # auto-format code
```

## Deployment

The app is configured for [Fly.io](https://fly.io) with a persistent SQLite volume. See `fly.toml` and `Dockerfile`.

```sh
fly deploy
```
