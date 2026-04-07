# Getting Started

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- [just](https://github.com/casey/just) (optional) — task runner

## Installation

```sh
# Clone the repo
git clone git@github.com:feds01/prbot.git
cd prbot

# Install dependencies and pre-commit hooks
just install
# or without just:
uv sync --dev && uv run pre-commit install
```

## Quick start

### 1. Configure credentials

Copy the example env file and fill in your credentials:

```sh
cp .env.example .env
```

You'll need GitHub credentials and at least one messaging integration — see the guides for how to obtain them:

- [GitHub integration](integrations/github.md) — create a GitHub App and get your app ID + private key + webhook secret
- [Slack integration](integrations/slack.md) — create a Slack app and get your bot token + signing secret
- [Discord integration](integrations/discord.md) — create a Discord bot and get your bot token

### 2. Upload custom emoji

prbot uses custom emoji for PR status reactions. Upload them to your Slack workspace or Discord server:

```sh
# Slack
uv run python scripts/upload_emojis.py slack --token xoxp-your-admin-token

# Discord
uv run python scripts/upload_emojis.py discord --token YOUR_BOT_TOKEN --guild-id 123456789
```

See [Custom emoji](configuration.md#custom-emoji) for details and customization options.

### 3. Run the server

```sh
just dev
# or:
uv run uvicorn prbot.main:api --reload
```

The server starts at `http://localhost:8080` with the following endpoints:

| Endpoint              | Method | Description                          |
| --------------------- | ------ | ------------------------------------ |
| `/slack/events`       | POST   | Slack event subscription             |
| `/github/webhooks`    | POST   | GitHub webhook receiver              |
| `/health`             | GET    | Health check                         |

!!! info "Discord"
    The Discord integration connects via WebSocket (Discord Gateway), so no HTTP endpoint is needed. It starts automatically when `PR_BOT_DISCORD__BOT_TOKEN` is set.

!!! tip "Local development with webhooks"
    To receive Slack events and GitHub webhooks locally, you'll need a tunnel. [ngrok](https://ngrok.com/) or [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) work well:

    ```sh
    ngrok http 8080
    ```

    Use the generated URL as your base URL when configuring Slack and GitHub.

### 3. Run checks

```sh
just check       # lint + format-check + typecheck + migration check
just test        # run tests
just lint-fix    # auto-fix lint issues
just format      # auto-format code
```

## Architecture

```mermaid
graph TB
    subgraph Integrations
        Slack[Slack]
        Discord[Discord]
    end

    subgraph Application
        HIM[Handle Incoming Message]
        HGW[Handle GitHub Webhook]
        RTP[Reconcile Tracked PRs]
    end

    subgraph Domain
        TE[TrackedPR Entity]
        VO[Value Objects]
        SR[Status Resolver]
    end

    subgraph Infrastructure
        GH[GitHub Gateway]
        DB[(SQLite)]
    end

    Slack -->|message events| HIM
    Discord -->|message events| HIM
    GH_WH[GitHub Webhooks] -->|PR events| HGW
    HIM --> GH
    HGW --> GH
    HIM --> DB
    HGW --> DB
    RTP --> HGW
    HIM --> SR
    HGW --> SR
    SR --> VO
```

prbot follows a **clean architecture** with ports and adapters:

- **Domain** — core business logic, no framework dependencies
- **Application** — use cases that orchestrate domain logic
- **Infrastructure** — external API clients (GitHub REST API)
- **Integration** — messaging platform adapters (Slack, Discord, etc.)
- **Data** — persistence with SQLAlchemy + async SQLite

### Startup behavior

On startup, prbot automatically:

1. **Runs database migrations** — schema is always up to date, no manual steps needed
2. **Backfills missed messages** — for each channel, fetches messages posted since the last tracked timestamp and processes any PR URLs found
3. **Reconciles tracked PRs** — re-checks all tracked PRs against GitHub to catch any webhook events missed during downtime

## Database migrations

Migrations run **automatically on startup** (step 1 above), so no manual steps are needed for deployment.

The project uses [Alembic](https://alembic.sqlalchemy.org/) for migrations. After modifying the SQLAlchemy models:

```sh
# Generate a new migration
uv run alembic revision --autogenerate -m "describe your change"

# Apply manually (if needed)
uv run alembic upgrade head

# Check current revision
uv run alembic current
```
