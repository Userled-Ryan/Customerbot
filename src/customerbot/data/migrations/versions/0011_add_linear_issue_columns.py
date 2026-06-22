"""add linear issue mapping columns to tickets

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-22

v1.5 mirrors every ticket into a Linear issue (dev handover + CTO reporting).
The mapping is strictly 1:1 with a ticket, so it lives as three nullable
columns on `tickets` rather than a side table:

- `linear_issue_id`         — Linear's internal UUID; used for issue mutations
                              and as the idempotency key (set once, then skip).
- `linear_issue_identifier` — human identifier e.g. "PRD-123" (display/logs).
- `linear_issue_url`        — deep link, surfaced in SE/stakeholder DMs.

Nullable + no backfill — existing rows keep these NULL until they are next
mirrored. `linear_issue_id` is indexed because the inbound Linear webhook looks
tickets up by it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("linear_issue_id", sa.String, nullable=True))
    op.add_column("tickets", sa.Column("linear_issue_identifier", sa.String, nullable=True))
    op.add_column("tickets", sa.Column("linear_issue_url", sa.String, nullable=True))
    op.create_index("idx_tickets_linear_issue_id", "tickets", ["linear_issue_id"])


def downgrade() -> None:
    op.drop_index("idx_tickets_linear_issue_id", table_name="tickets")
    op.drop_column("tickets", "linear_issue_url")
    op.drop_column("tickets", "linear_issue_identifier")
    op.drop_column("tickets", "linear_issue_id")
