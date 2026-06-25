# Deployment

## Fly.io

customerbot is pre-configured for [Fly.io](https://fly.io) with a
persistent SQLite volume. Deploy target is `customerbot-userled.fly.dev`.

### 1. Install flyctl

```sh
# macOS
brew install flyctl

# or via script
curl -L https://fly.io/install.sh | sh
```

### 2. Create the app

```sh
fly launch --no-deploy
```

### 3. Create a volume for the database

```sh
fly volumes create customerbot_data --size 1 --region lhr
```

!!! note
    Replace `lhr` with your preferred [Fly.io region](https://fly.io/docs/reference/regions/).
    Keep the volume name as `customerbot_data` to match the `[mounts]`
    `source` in `fly.toml`.

### 4. Set secrets

```sh
fly secrets set \
  CUSTOMERBOT_SLACK__BOT_TOKEN="xoxb-..." \
  CUSTOMERBOT_SLACK__SIGNING_SECRET="..." \
  CUSTOMERBOT_SLACK__WORKSPACE_URL="https://yourcompany.slack.com" \
  CUSTOMERBOT_SE_USER_ID="U0123456789" \
  CUSTOMERBOT_CTO_USER_ID="U..." \
  CUSTOMERBOT_TECH_ASSISTANCE_CHANNEL_ID="C..." \
  CUSTOMERBOT_SE_TICKETS_CHANNEL_ID="C..." \
  CUSTOMERBOT_SUPPORT_PING_CHANNEL_ID="C..." \
  CUSTOMERBOT_INTERNAL_USER_GROUP_ID="S..." \
  CUSTOMERBOT_SUPPORT_HANDLE="S..." \
  CUSTOMERBOT_SE_TIMEZONE="Europe/London" \
  CUSTOMERBOT_INAPP_WEBHOOK_SECRET="$(openssl rand -hex 32)"
```

See [Configuration](configuration.md) for what each gates.

### 5. Deploy

```sh
fly deploy
```

The app will be available at `https://customerbot-userled.fly.dev`.

### CI/CD

The included GitHub Actions workflow (`.github/workflows/ci.yml`)
deploys to Fly.io on pushes to `main` after every check passes (ruff,
ty, import-linter, pytest). Set the `FLY_API_TOKEN` secret in the
GitHub repository settings.

## Docker (self-host)

```sh
docker build -t customerbot .
docker run -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  customerbot
```

??? example "Dockerfile"
    ```dockerfile
    FROM python:3.14-slim
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
    WORKDIR /app
    COPY pyproject.toml uv.lock ./
    RUN uv sync --frozen --no-dev --no-install-project
    COPY . .
    RUN uv sync --frozen --no-dev
    CMD ["uv", "run", "uvicorn", "customerbot.main:api", "--host", "0.0.0.0", "--port", "8080"]
    ```

## Production checklist

- [ ] Slack app created from `slack-manifest.yml` and installed in the
      workspace. After any manifest change → reinstall, otherwise new
      scopes don't take effect.
- [ ] `customerbot-userled.fly.dev` (or your host) substituted into the manifest's
      event / interactivity URLs.
- [ ] All `CUSTOMERBOT_*` env keys set on the deployment (see [Configuration](configuration.md)).
- [ ] At least one `orgs` row exists with `slack_channel_id` and
      `csm_user_id` set. Seed via `scripts/seed_org.py` (single org) or
      `scripts/import_orgs.py` (bulk) — run on the Fly machine over
      `fly ssh console`.
- [ ] `prio_matrix.yaml` mounted / committed; calibrated weights for
      ACV × sentiment × renewal.
- [ ] Fly volume `data` is persistent (the SQLite DB lives there).
- [ ] `/health` returns `{"status": "healthy"}`.
- [ ] Walk the four happy paths from
      [`docs/specs/smoke-test.md`](specs/smoke-test.md). The automated
      test suite covers code paths; the smoke test catches
      Slack-scope / channel-config issues that unit tests can't see.

## Background-task observability

Every job started by `customerbot.main.lifespan` registers a
`done_callback` that logs cancellation + unhandled exceptions. Watch
the structured logs for these task names:

| Task | Cadence |
|---|---|
| `bot-state-sweeper` | 1 min |
| `p0-candidate-scan` | 30 min |
| `monthly-matrix-review` | 5 min poll |
| `sla-state-machine` | 15 min |
| `auto-close-awaiting` | daily |
| `reply-needed-digest` | 30 min poll |
| `weekly-digest` | 30 min poll |

The auto-close, weekly-digest, and matrix-review jobs are
**time-window** jobs — they poll frequently but only act inside their
SE-local firing windows, then idempotently throttle via singleton
state rows.
