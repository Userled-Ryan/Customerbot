"""Bulk-import / update the `orgs` table from a CSV.

The orgs table is what populates the customer dropdown in the intake modals,
so this is how you load your customer list in one shot instead of running
`seed_org.py` once per customer.

Idempotent: rows are upserted by `id`, so re-running with an updated CSV
patches existing customers and adds new ones. Nothing is deleted — a customer
dropped from the CSV stays in the table (remove those by hand if needed).

CSV format — a header row, then one row per customer. Recognised columns
(only `id` and `name` are required; everything else is optional and may be
left blank):

    id                 short slug, e.g. "acme"  (primary key — keep stable)
    name               display name shown in the dropdown
    slack_channel_id   Slack channel ID (C…), blank for Teams-only customers
    teams_channel_id   Microsoft Teams channel ID, blank for Slack customers
    csm_user_id        CSM's Slack user ID (U…)
    acv_tier           one of: small | mid | large | enterprise
    sentiment          one of: positive | neutral | negative
    renewal_status     one of: stable | at-risk | churning  (see value_objects)
    renewal_date       YYYY-MM-DD

Unknown columns are ignored, so you can keep extra notes columns in your CSV.
Header matching is case-insensitive and tolerates spaces/hyphens.

Reads `CUSTOMERBOT_DATABASE_PATH` from the environment (same as the app), so on
the Fly machine it writes to the live SQLite volume.

Usage:

    uv run --no-sync python scripts/import_orgs.py customers.csv
    uv run --no-sync python scripts/import_orgs.py customers.csv --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime

from customerbot.data.database import (
    database_url_from_path,
    make_engine,
    make_session_factory,
)
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.domain.tickets.entities import Org, customer_weight
from customerbot.domain.tickets.value_objects import ACVTier, RenewalStatus, Sentiment

_DATE_FMT = "%Y-%m-%d"

# Map a normalised header (lowercased, spaces/hyphens → underscore) to the
# Org field it feeds. Keeps the CSV forgiving about header spelling.
_ALIASES = {
    "id": "id",
    "org_id": "id",
    "slug": "id",
    "name": "name",
    "org_name": "name",
    "slack_channel_id": "slack_channel_id",
    "slack_channel": "slack_channel_id",
    "slack": "slack_channel_id",
    "teams_channel_id": "teams_channel_id",
    "teams_channel": "teams_channel_id",
    "teams": "teams_channel_id",
    "csm_user_id": "csm_user_id",
    "csm": "csm_user_id",
    "acv_tier": "acv_tier",
    "acv": "acv_tier",
    "sentiment": "sentiment",
    "renewal_status": "renewal_status",
    "renewal": "renewal_status",
    "renewal_date": "renewal_date",
}


def _norm(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


class RowError(ValueError):
    """A CSV row that can't be turned into a valid Org."""


def _row_to_org(raw: dict[str, str], *, line_no: int) -> Org:
    # Re-key the row by canonical field name, dropping unknown columns.
    fields: dict[str, str | None] = {}
    for header, value in raw.items():
        if header is None:
            continue
        key = _ALIASES.get(_norm(header))
        if key is not None:
            fields[key] = _clean(value)

    org_id = fields.get("id")
    name = fields.get("name")
    if not org_id:
        raise RowError(f"line {line_no}: missing required 'id'")
    if not name:
        raise RowError(f"line {line_no}: missing required 'name' (id={org_id!r})")

    try:
        acv = ACVTier(fields["acv_tier"]) if fields.get("acv_tier") else None
        sentiment = Sentiment(fields["sentiment"]) if fields.get("sentiment") else None
        renewal = RenewalStatus(fields["renewal_status"]) if fields.get("renewal_status") else None
    except ValueError as exc:
        raise RowError(f"line {line_no} (id={org_id!r}): {exc}") from exc

    renewal_date = None
    if fields.get("renewal_date"):
        try:
            renewal_date = datetime.strptime(fields["renewal_date"], _DATE_FMT).date()  # type: ignore[arg-type]
        except ValueError as exc:
            raise RowError(
                f"line {line_no} (id={org_id!r}): bad renewal_date "
                f"{fields['renewal_date']!r}, expected YYYY-MM-DD"
            ) from exc

    return Org(
        id=org_id,
        name=name,
        slack_channel_id=fields.get("slack_channel_id"),
        teams_channel_id=fields.get("teams_channel_id"),
        csm_user_id=fields.get("csm_user_id"),
        acv_tier=acv,
        sentiment=sentiment,
        renewal_status=renewal,
        renewal_date=renewal_date,
    )


def _parse_csv(path: str) -> tuple[list[Org], list[str]]:
    orgs: list[Org] = []
    errors: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty file or no header row")
        # `line_no` counts the header as line 1, so data starts at 2.
        for line_no, raw in enumerate(reader, start=2):
            try:
                orgs.append(_row_to_org(raw, line_no=line_no))
            except RowError as exc:
                errors.append(str(exc))
    return orgs, errors


async def _import(orgs: list[Org], db_path: str) -> None:
    engine = make_engine(database_url_from_path(db_path))
    factory = make_session_factory(engine)
    repo = SQLiteOrgRepository(factory)
    for org in orgs:
        await repo.upsert(org)
    await engine.dispose()


def _summarise(org: Org) -> str:
    weight = customer_weight(org.acv_tier, org.sentiment, org.renewal_status)
    channel = org.slack_channel_id or (
        f"teams:{org.teams_channel_id}" if org.teams_channel_id else "—"
    )
    return f"  {org.id:<20} {org.name:<30} channel={channel} weight={weight.value}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk import/update orgs from a CSV.")
    parser.add_argument("csv_path", help="Path to the customers CSV")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate the CSV, print what would change, but don't write.",
    )
    args = parser.parse_args()

    orgs, errors = _parse_csv(args.csv_path)

    if errors:
        print(f"Found {len(errors)} bad row(s):", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        print("Fix these and re-run — nothing was written.", file=sys.stderr)
        raise SystemExit(1)

    if not orgs:
        raise SystemExit(f"{args.csv_path}: no data rows found")

    db_path = os.environ.get("CUSTOMERBOT_DATABASE_PATH", "data/customerbot.db")
    print(f"Parsed {len(orgs)} org(s) from {args.csv_path}:")
    for org in orgs:
        print(_summarise(org))

    if args.dry_run:
        print(f"\n[dry-run] Would upsert {len(orgs)} org(s) into {db_path}. No changes made.")
        return

    asyncio.run(_import(orgs, db_path))
    print(f"\n✅ Upserted {len(orgs)} org(s) into {db_path}.")


if __name__ == "__main__":
    main()
