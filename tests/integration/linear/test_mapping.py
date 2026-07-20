from __future__ import annotations

from customerbot.domain.linear.ports import LinearWorkflowState
from customerbot.domain.tickets.entities import Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Priority,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from customerbot.integration.linear.mapping import (
    InboundIntent,
    build_issue_description,
    build_issue_title,
    first_github_pr_url,
    linear_state_to_inbound_intent,
    ticket_priority_to_linear,
    ticket_to_linear_state,
)


def _ticket(**kw: object) -> Ticket:
    base: dict[str, object] = dict(
        id=7,
        title="Publishing fails on Safari",
        type=TicketType.BUG,
        subtype=TicketSubtype.PLATFORM_WIDE,
        reporter_user_id="U_SE",
        source=Source.CUSTOMER_CHANNEL,
        description="Crashes on iOS 18.",
    )
    base.update(kw)
    return Ticket(**base)  # type: ignore[arg-type]


def test_forward_state_mapping_by_status() -> None:
    assert ticket_to_linear_state(TicketStatus.NEW, None) == LinearWorkflowState.TRIAGE
    assert ticket_to_linear_state(TicketStatus.IN_PROGRESS, None) == LinearWorkflowState.IN_PROGRESS
    assert (
        ticket_to_linear_state(TicketStatus.AWAITING_CUSTOMER, None)
        == LinearWorkflowState.AWAITING_CUSTOMER
    )
    assert ticket_to_linear_state(TicketStatus.RESOLVED, None) == LinearWorkflowState.DONE
    assert ticket_to_linear_state(TicketStatus.CLOSED, None) == LinearWorkflowState.DONE


def test_dev_lane_new_is_in_progress_not_triage() -> None:
    # A ticket handed to dev is being worked, so it shows as In Progress.
    assert (
        ticket_to_linear_state(TicketStatus.NEW, Lane.DEV_ACTION) == LinearWorkflowState.IN_PROGRESS
    )


def test_reverse_intent_mapping() -> None:
    assert linear_state_to_inbound_intent(LinearWorkflowState.DONE) == InboundIntent.RESOLVE
    assert linear_state_to_inbound_intent(LinearWorkflowState.CANCELED) == InboundIntent.DROP
    assert (
        linear_state_to_inbound_intent(LinearWorkflowState.IN_PROGRESS)
        == InboundIntent.REOPEN_IN_PROGRESS
    )
    assert linear_state_to_inbound_intent(LinearWorkflowState.TRIAGE) == InboundIntent.NONE
    assert (
        linear_state_to_inbound_intent(LinearWorkflowState.AWAITING_CUSTOMER) == InboundIntent.NONE
    )


def test_priority_mapping_is_monotonic_into_linear_scale() -> None:
    assert ticket_priority_to_linear(Priority.P0) == 1  # urgent
    assert ticket_priority_to_linear(Priority.P1) == 2  # high
    assert ticket_priority_to_linear(Priority.P4) == 4  # low
    # Non-increasing urgency as the tier drops.
    tiers = (Priority.P0, Priority.P1, Priority.P2, Priority.P3, Priority.P4)
    seq = [ticket_priority_to_linear(p) for p in tiers]
    assert seq == sorted(seq)


def test_title_is_prefixed_with_bosh_id() -> None:
    # No orgs → just the Bosh id prefix.
    assert build_issue_title(_ticket(), []) == "Bosh-007 · Publishing fails on Safari"


def test_title_is_prefixed_with_company() -> None:
    # Single org groups the ticket under the company name.
    assert (
        build_issue_title(_ticket(), ["Stripe"]) == "Stripe · Bosh-007 · Publishing fails on Safari"
    )
    # Multiple orgs are joined.
    assert (
        build_issue_title(_ticket(), ["Stripe", "Globex"])
        == "Stripe, Globex · Bosh-007 · Publishing fails on Safari"
    )


def test_description_includes_orgs_links_and_display_id() -> None:
    t = _ticket(
        severity=Severity.BLOCKING,
        original_slack_link="https://x.slack.com/archives/C1/p123",
        prod_link="https://app.example.com/x",
        campaign_url="https://app.example.com/campaigns/42",
    )
    body = build_issue_description(t, ["Acme Corp", "Globex"])
    assert "TIC-007" in body
    assert "Acme Corp, Globex" in body
    assert "Original thread" in body
    assert "Prod link" in body
    assert "[Campaign](https://app.example.com/campaigns/42)" in body
    assert "Crashes on iOS 18." in body


def test_description_includes_slack_card_link_when_given() -> None:
    t = _ticket()
    link = "https://x.slack.com/archives/C_SE/p1700000000000100"
    body = build_issue_description(t, ["Acme"], slack_link=link)
    assert f"[Manage in Slack]({link})" in body
    # Absent when no link is passed.
    assert "Manage in Slack" not in build_issue_description(t, ["Acme"])


def test_description_converts_slack_mrkdwn_links_to_markdown() -> None:
    # Slack auto-links bare URLs as `<url|display>`; passing that to Linear
    # verbatim leaves a duplicated `url|display`. It should become a real link.
    url = "https://app.userled.io/campaigns/eb63/distribute/linkedin"
    display = "app.userled.io/campaigns/eb63/distribute/linkedin"
    t = _ticket(description=f"Campaign: <{url}|{display}>")
    body = build_issue_description(t, ["Acme"])
    assert f"[{display}]({url})" in body
    assert f"{url}|{display}" not in body


def test_description_normalizes_bare_links_and_entities() -> None:
    # Bare auto-link (no distinct label) collapses to the URL; Slack HTML
    # entities are unescaped (incl. `&amp;` in a query string).
    t = _ticket(description="see <https://ex.com/a?x=1&amp;y=2> &lt;now&gt;")
    body = build_issue_description(t, ["Acme"])
    assert "see https://ex.com/a?x=1&y=2 <now>" in body


def test_first_github_pr_url_prefers_first_match_and_ignores_non_prs() -> None:
    assert first_github_pr_url([None, "", "no url here"]) is None
    # A plain repo/issue link is not a PR.
    assert first_github_pr_url(["https://github.com/acme/app/issues/9"]) is None
    pr = "https://github.com/acme/app/pull/42"
    assert first_github_pr_url(["notes", pr, "https://github.com/x/y/pull/1"]) == pr
    # Found embedded in free text (e.g. a description body).
    assert first_github_pr_url([f"fixed by {pr} 🎉"]) == pr
