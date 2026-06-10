"""Seed or update a row in the `orgs` table.

The orgs table maps a customer → their Slack channel + CSM + the weighting
inputs (ACV × sentiment × renewal) the priority matrix reads. The bot won't
show a usable intake modal until at least one org exists, and an unmapped
org_id (e.g. a Gleap in-app submission for a customer not yet in the table)
falls back to the `unknown` catch-all org — so seeding that catch-all is part
of first-run setup.

Reads `CUSTOMERBOT_DATABASE_PATH` from the environment (same as the app), so on
the Fly machine it writes to the live SQLite volume. Idempotent — re-running
with the same `--id` updates the existing row.

Usage (on the Fly machine, via `fly ssh console`):

    uv run --no-sync python scripts/seed_org.py \
        --id unknown --name "Unknown (unmapped customer)" \
        --acv enterprise --sentiment negative --renewal at-risk --csm U08AL6BAAQN

    uv run --no-sync python scripts/seed_org.py \
        --id acme --name "Acme Corp" --channel C0123ABCD --csm U0456 \
        --acv large --sentiment neutral --renewal stable
"""

from __future__ import annotations

import argparse
import asyncio
import os

from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.domain.tickets.entities import Org, customer_weight
from customerbot.domain.tickets.value_objects import ACVTier, RenewalStatus, Sentiment


async def _run(args: argparse.Namespace) -> None:
    db_path = os.environ.get("CUSTOMERBOT_DATABASE_PATH", "data/customerbot.db")
    engine = make_engine(database_url_from_path(db_path))
    factory = make_session_factory(engine)
    orgs = SQLiteOrgRepository(factory)

    acv = ACVTier(args.acv) if args.acv else None
    sentiment = Sentiment(args.sentiment) if args.sentiment else None
    renewal = RenewalStatus(args.renewal) if args.renewal else None

    org = Org(
        id=args.id,
        name=args.name,
        slack_channel_id=args.channel,
        csm_user_id=args.csm,
        acv_tier=acv,
        sentiment=sentiment,
        renewal_status=renewal,
    )
    await orgs.upsert(org)
    await engine.dispose()

    weight = customer_weight(acv, sentiment, renewal)
    print(
        f"Upserted org {org.id!r} ({org.name}) into {db_path}\n"
        f"  channel={org.slack_channel_id} csm={org.csm_user_id}\n"
        f"  acv={args.acv} sentiment={args.sentiment} renewal={args.renewal} "
        f"→ customer_weight={weight.value}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or update an orgs row.")
    parser.add_argument("--id", required=True, help="Short org slug, e.g. 'acme'")
    parser.add_argument("--name", required=True, help="Display name")
    parser.add_argument("--channel", default=None, help="Customer Slack channel ID (C…)")
    parser.add_argument("--csm", default=None, help="CSM Slack user ID (U…)")
    parser.add_argument("--acv", default=None, choices=[t.value for t in ACVTier])
    parser.add_argument("--sentiment", default=None, choices=[s.value for s in Sentiment])
    parser.add_argument("--renewal", default=None, choices=[r.value for r in RenewalStatus])
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
