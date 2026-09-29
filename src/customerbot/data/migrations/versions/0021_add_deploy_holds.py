"""add releases, release_prs and deploy_holds tables

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29

Moving a Linear issue to Done usually means its PR is *merged*, not *deployed*.
The customer-facing "resolved" thread reply now waits for the PR to ship:

- `releases` / `release_prs` record every release thread seen in #engineering
  (the "These commits are about to be merged into release!" post) and the PR
  numbers it carries. `deployed_at` is set once the GitHub Actions run link is
  posted in the thread.
- `deploy_holds` is one row per (ticket, PR) whose resolved reply is waiting on
  that PR. Open until released (reply posted) or cancelled (ticket reopened).
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column("release_ts", sa.String, primary_key=True),
        sa.Column("channel_id", sa.String, nullable=False),
        sa.Column("run_url", sa.String, nullable=True),
        sa.Column("deployed_at", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_table(
        "release_prs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("release_ts", sa.String, sa.ForeignKey("releases.release_ts"), nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=False),
        sa.UniqueConstraint("release_ts", "pr_number"),
    )
    op.create_index("idx_release_prs_pr_number", "release_prs", ["pr_number"])
    op.create_table(
        "deploy_holds",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=False),
        sa.Column("pr_url", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("deployed_at", sa.String, nullable=True),
        sa.Column("nudged_at", sa.String, nullable=True),
        sa.Column("released_at", sa.String, nullable=True),
        sa.Column("cancelled_at", sa.String, nullable=True),
        sa.UniqueConstraint("ticket_id", "pr_number"),
    )
    op.create_index("idx_deploy_holds_pr_number", "deploy_holds", ["pr_number"])


def downgrade() -> None:
    op.drop_index("idx_deploy_holds_pr_number", table_name="deploy_holds")
    op.drop_table("deploy_holds")
    op.drop_index("idx_release_prs_pr_number", table_name="release_prs")
    op.drop_table("release_prs")
    op.drop_table("releases")
