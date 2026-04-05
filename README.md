# prbot

A bot that watches for GitHub PR URLs in your messages and reacts with emoji reflecting the PR's current status. When a PR's status changes, the bot automatically updates the reaction.

```mermaid
flowchart TB
    subgraph integrations [" Messaging Integrations"]
        direction LR
        slack["<img src='https://cdn.simpleicons.org/slack/E01E5A' width='20' height='20' /> Slack"]
        discord["<img src='https://cdn.simpleicons.org/discord/5865F2' width='20' height='20' /> Discord"]
        future["+ Future Integrations"]
        style future stroke-dasharray: 5 5, fill:none
    end

    subgraph bot [" prbot"]
        direction TB
        detect["Detect PR URLs\nin messages"]
        resolve["Resolve PR Status"]
        react["Add Emoji Reaction"]
        detect --> resolve --> react
    end

    subgraph sources [" Code Hosting"]
        direction LR
        github["<img src='https://cdn.simpleicons.org/github/white' width='20' height='20' /> GitHub"]
        future_src["+ Future Sources"]
        style future_src stroke-dasharray: 5 5, fill:none
    end

    slack -- "messages" --> bot
    discord -- "messages" --> bot
    future -. "messages" .-> bot

    bot -- "emoji reactions" --> slack
    bot -- "emoji reactions" --> discord
    bot -. "emoji reactions" .-> future

    github -- "PR status &\nwebhooks" --> bot
    future_src -. "PR status" .-> bot

    style integrations fill:#1a1a2e,stroke:#4a4a6a,color:#fff
    style bot fill:#0d1117,stroke:#58a6ff,color:#fff
    style sources fill:#1a1a2e,stroke:#4a4a6a,color:#fff
    style slack fill:#2d1a35,stroke:#E01E5A,color:#fff
    style discord fill:#1a1a3e,stroke:#5865F2,color:#fff
    style github fill:#161b22,stroke:#8b949e,color:#fff
    style detect fill:#21262d,stroke:#58a6ff,color:#c9d1d9
    style resolve fill:#21262d,stroke:#58a6ff,color:#c9d1d9
    style react fill:#21262d,stroke:#58a6ff,color:#c9d1d9
```

| PR Status         | Emoji                      |
| ----------------- | -------------------------- |
| Open              | :eyes:                     |
| Approved          | :white_check_mark:         |
| Changes requested | :arrows_counterclockwise:  |
| Commented         | :speech_balloon:           |
| Merged            | :tada:                     |
| Closed            | :x:                        |

## Documentation

Full documentation is available at **[feds01.github.io/prbot](https://feds01.github.io/prbot)**.

- [Getting Started](https://feds01.github.io/prbot/getting-started/) — install and run locally
- [Slack Integration](https://feds01.github.io/prbot/integrations/slack/) — create a Slack app
- [GitHub Integration](https://feds01.github.io/prbot/integrations/github/) — create a GitHub App
- [Configuration](https://feds01.github.io/prbot/configuration/) — environment variables & custom emoji
- [Deployment](https://feds01.github.io/prbot/deployment/) — Fly.io, Docker & self-hosting

## Quick start

```sh
git clone git@github.com:feds01/prbot.git
cd prbot
just install
cp .env.example .env  # fill in credentials
just dev
```

## Development

```sh
just check       # lint + format + typecheck + migration check
just test        # run tests
just docs        # serve docs locally
```

## License

MIT
