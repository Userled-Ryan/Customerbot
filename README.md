# customerbot

A Slack-resident bot that turns customer-surfaced queries into structured
tickets, drafts customer replies for the Solutions Engineer (SE) to send,
fires SLA alerts, and keeps a live "board" message updated so anyone on
the team can see what's open without asking.

## Two design rules

1. **Bot suggests, human decides.** The bot drafts every customer-facing
   message, fills every form, and surfaces every prio bump — SE (or the
   CSM) clicks the button. The bot never messages a customer directly.
2. **Append-only event log.** Every state change writes to one of four
   `event_*` tables. Reporting metrics — reclassification rate, SLA
   breach rate, first-response time by tier — fall out of those rows.

## Features

- **`/log-ticket`** — open the SE-bug or CSM-intake modal, validate, dedupe,
  create the ticket, draft the §9a customer-facing acknowledgement.
- **`log` / `check` detector** — internal members typing `log` or `check`
  in a customer thread get a DM with a pre-filled intake form.
- **Suggest-not-auto dedupe** — token-overlap + prod-link + feature-tag
  scoring; SE clicks **Merge** or **Create new**.
- **Priority pipeline** — YAML matrix lookup, multi-customer bump
  suggestions, P0-candidate scan, monthly weightings review. Customer
  weight = ACV × sentiment × renewal, where renewal *proximity* steps the
  weight up as the contract renewal date nears (×1.25 at ≤6 months, ×1.5
  at ≤3 months / overdue).
- **CSM stakeholders** — the affected org's CSM (`orgs.csm_user_id`) is
  `@`-mentioned on the ticket card so they're looped in and can follow
  progress without being the SE.
- **SLA state machine** — green / amber / red clocks per stage; SE gets
  one DM on each escalation, no spam. Pauses on Awaiting customer.
- **Lifecycle** — ticket-card buttons (Resolved · Resolved via hotfix ·
  Move to Dev Action · Reclassify · Add affected org · **Drop** to close
  now) plus Set deadline and (FAQ-only) Needs article. Closed cards
  collapse to a single Reopen; the header shows the stage at a glance
  (check when awaiting/resolved, lock when closed).
- **Org roster** — customers live in the `orgs` table (name, Slack *or*
  Microsoft Teams channel, CSM, ACV/sentiment/renewal). Seed one with
  `scripts/seed_org.py` or bulk-import a CSV with `scripts/import_orgs.py`.
- **Customer-comms drafts** — §9a–§9e templates DM'd to SE at the right
  cadence (initial ack, periodic update, resolution, nudge, auto-close).
- **Reclassification with audit** — bot drafts the §9f internal alert,
  SE picks who it goes to, no customer ever receives it.
- **Articles workflow** — FAQ tickets close immediately; the related
  article lands on a separate `/board articles` queue.
- **In-app webhook** — `POST /webhooks/in-app-bug` with HMAC-SHA256
  signature; tickets created with `Source.IN_APP`, dedupe runs, feed
  entry posted to `#tech-assistance`.
- **Weekly digest** — Monday 09:00 SE-local: counts by tier, breach
  rate, oldest open per tier.

## Documentation

Full guides live under [`docs/`](docs/):

- [Getting Started](docs/getting-started.md) — local install + first run
- [Slack Integration](docs/integrations/slack.md) — create the app from
  the v1 manifest
- [Configuration](docs/configuration.md) — every `CUSTOMERBOT_*` env var
- [Commands](docs/commands.md) — `/log`, `/board`, ticket-card buttons
- [Deployment](docs/deployment.md) — Fly.io recipe + production checklist

Implementation specs (the design source-of-truth) are under
[`docs/specs/`](docs/specs/):

- `se-ticketing-flow-v1.md` — the customer-side flow spec
- `customerbot-min-spec.md` — the build spec (forms, templates, scopes,
  §14 build checklist)
- `implementation-plan.md` — the 15-chunk delivery plan
- `smoke-test.md` — manual end-to-end verification once deployed

## Quick start

```sh
git clone git@github.com:Userled-Ryan/Customerbot.git
cd Customerbot
uv sync --dev
uv run pre-commit install

cp .env.example .env   # fill in CUSTOMERBOT_SLACK__* and CUSTOMERBOT_SE_USER_ID
just dev               # or: uv run uvicorn customerbot.main:api --reload
```

The server starts at `http://localhost:8080` with `/slack/events`,
`/webhooks/in-app-bug`, and `/health` endpoints. Database migrations
run automatically on startup.

## Development

```sh
just check       # ruff lint + format-check + ty + import-linter + migration check
just test        # pytest (274 tests at v1)
just lint-fix    # auto-fix lint
just format      # auto-format
just docs        # mkdocs serve
```

Quality gate per PR: ruff lint + format clean, `ty check` clean,
`lint-imports` keeps all 8 layered-architecture contracts, full pytest
green.

## Architecture

Layered, ports-and-adapters:

```
customerbot.main         → boots FastAPI + lifespan tasks
customerbot.integration  → Slack handler, webhook router (the only
                           layer that imports Slack/FastAPI/Pydantic)
customerbot.data         → SQLAlchemy repos + Alembic migrations
customerbot.application  → use cases — pure orchestration, no I/O
customerbot.config       → settings loader
customerbot.domain       → entities + value objects + ports (vendor-free)
```

`import-linter` enforces the layering and forbids vendor SDKs in
`domain` / `application`.

## License

Internal — Userled property.
