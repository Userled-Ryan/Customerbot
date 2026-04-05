# prbot

A bot that watches for GitHub PR URLs in your messages and reacts with emoji reflecting the PR's current status. When a PR's status changes, the bot automatically updates the reaction.

```mermaid
flowchart LR
    subgraph chat [" Someone shares a PR link"]
        msg["hey check out my PR\ngithub.com/org/repo/pull/42"]
    end

    subgraph prbot [" prbot watches and reacts"]
        direction TB
        look["Spots the PR link"]
        check["Checks the status on GitHub"]
        emoji["Reacts with the right emoji"]
        look --> check --> emoji
    end

    subgraph result [" Your team sees"]
        reaction["The message gets a\nlive status emoji\nthat updates automatically"]
    end

    chat --> prbot --> result

    style chat fill:#1a1a2e,stroke:#4a4a6a,color:#c9d1d9
    style prbot fill:#0d1117,stroke:#58a6ff,color:#c9d1d9
    style result fill:#1a1a2e,stroke:#4a4a6a,color:#c9d1d9
    style msg fill:#21262d,stroke:#8b949e,color:#c9d1d9
    style look fill:#21262d,stroke:#58a6ff,color:#c9d1d9
    style check fill:#21262d,stroke:#58a6ff,color:#c9d1d9
    style emoji fill:#21262d,stroke:#58a6ff,color:#c9d1d9
    style reaction fill:#21262d,stroke:#3fb950,color:#c9d1d9
```

Works with **Slack**, **Discord**, and more integrations coming soon.

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
