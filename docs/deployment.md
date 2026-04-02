# Deployment

## Fly.io

prbot is pre-configured for [Fly.io](https://fly.io) with a persistent SQLite volume.

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
fly volumes create data --size 1 --region lhr
```

!!! note
    Replace `lhr` with your preferred [Fly.io region](https://fly.io/docs/reference/regions/).

### 4. Set secrets

```sh
fly secrets set \
  PR_BOT_GITHUB_APP_ID="your-app-id" \
  PR_BOT_GITHUB_PRIVATE_KEY="$(cat path/to/private-key.pem)" \
  PR_BOT_GITHUB_WEBHOOK_SECRET="your-webhook-secret" \
  PR_BOT_SLACK__BOT_TOKEN="xoxb-your-token" \
  PR_BOT_SLACK__SIGNING_SECRET="your-signing-secret"
```

### 5. Deploy

```sh
fly deploy
```

The app will be available at `https://your-app-name.fly.dev`.

### CI/CD

The included GitHub Actions workflow (`.github/workflows/ci.yml`) automatically deploys to Fly.io on pushes to `main` after all checks pass. Set the `FLY_API_TOKEN` secret in your GitHub repository settings.

## Docker

Build and run with Docker directly:

```sh
docker build -t prbot .
docker run -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  prbot
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
    CMD ["uv", "run", "uvicorn", "prbot.main:api", "--host", "0.0.0.0", "--port", "8080"]
    ```

## Self-hosting checklist

Before going to production, make sure you have:

- [x] Created and installed a [Slack app](integrations/slack.md)
- [x] Created and installed a [GitHub App](integrations/github.md)
- [x] Set all required [environment variables](configuration.md)
- [x] Configured your webhook URLs to point to your deployment:
    - Slack: `https://your-domain.com/slack/events`
    - GitHub: `https://your-domain.com/github/webhooks`
- [x] Ensured the database volume is persistent (data is not lost on redeploy)
- [x] Verified the `/health` endpoint returns `{"status": "healthy"}`
