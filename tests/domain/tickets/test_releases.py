from __future__ import annotations

from customerbot.domain.tickets.releases import (
    is_release_announcement,
    parse_pr_numbers,
    parse_run_url,
    pr_number_from_url,
)

REPO = "userledio/core"

# Trimmed from a real #engineering release post.
RELEASE_POST = """These commits are about to be merged into release!

--------------------------------------------------
<@U0BU20CGKL6|Nic> 45aec5192 [PRO-1666]: Cut audience sweep allocations (#9790) (12 minutes ago)
<@U09LLPAM70S|Alex> bdfefb05a ci: tag every prod deploy's images prod-<tag> (#9810) (27 minutes ago)
<@U0BEZCALK0E|Elizaveta Makarina> a22449283 [Bosh-402]: Hide engagement charts (#9827) (2 hours ago)
<@U09LLPAM70S|Alex> c44ff0fb4 [PRO-1445]: Drop CBC on `<http://api.userled.io|api>` (#9768) (5m)
--------------------------------------------------
No migrations required

Are you sure? [y/N]"""


def test_release_announcement_detected() -> None:
    assert is_release_announcement(RELEASE_POST)
    assert not is_release_announcement("ADR for Gong - <https://app.notion.com/p/x|x>")


def test_pr_numbers_parsed_with_and_without_linear_ids() -> None:
    assert parse_pr_numbers(RELEASE_POST) == {9790, 9810, 9827, 9768}


def test_pr_numbers_from_queued_commits_reply() -> None:
    reply = (
        "Queueing these for release as well:\n"
        "<@U0AM73X1MA5|Ade> 0b63a79f0 Stop logo marquee overflowing page on mobile (#9755) "
        "(35 minutes ago)"
    )
    assert parse_pr_numbers(reply) == {9755}
    assert parse_pr_numbers("added new commits") == set()


def test_run_url_parsed_from_slack_mrkdwn_link() -> None:
    text = (
        "<https://github.com/userledio/core/actions/runs/36125460700"
        "|github.com/userledio/core/…/36125460700>"
    )
    assert parse_run_url(text, REPO) == "https://github.com/userledio/core/actions/runs/36125460700"
    assert parse_run_url("<https://github.com/userledio/core/actions/runs/1>", REPO) is not None


def test_run_url_ignores_other_repos_and_non_run_links() -> None:
    assert parse_run_url("https://github.com/other/app/actions/runs/1", REPO) is None
    assert parse_run_url("https://github.com/userledio/core/pull/9790", REPO) is None


def test_pr_number_from_url_only_for_watched_repo() -> None:
    assert pr_number_from_url("https://github.com/userledio/core/pull/9790", REPO) == 9790
    assert pr_number_from_url("https://github.com/userledio/customerbot/pull/54", REPO) is None
    assert pr_number_from_url("https://github.com/userledio/core/issues/9", REPO) is None
