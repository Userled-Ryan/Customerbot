"""`/ooo` sick / holiday mode: absences reroute new tickets and can move open ones."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from customerbot.application.intake.apply_se_owner import ApplySeOwnerChange
from customerbot.application.intake.availability import MarkBack, MarkOoo, SeAvailability
from customerbot.application.intake.dedupe import FindDedupeCandidate, OfferDedupeChoice
from customerbot.application.intake.submissions import SEBugSubmission
from customerbot.application.intake.submit_ticket_form import SubmitTicketForm
from customerbot.application.linear.sync import LinearSync
from customerbot.application.priority.assign import AssignPriority
from customerbot.application.priority.matrix import PriorityMatrix
from customerbot.data.repository.availability import SQLiteSeAvailabilityRepository
from customerbot.data.repository.bot_state import (
    SQLiteDraftFormSessionRepository,
    SQLitePendingDedupeChoiceRepository,
)
from customerbot.data.repository.event_logs import SQLiteEventLogRepository
from customerbot.data.repository.orgs import SQLiteOrgRepository
from customerbot.data.repository.tickets import SQLiteTicketRepository
from customerbot.domain.tickets.entities import Org, SeAbsence, Ticket
from customerbot.domain.tickets.value_objects import (
    Lane,
    Severity,
    Source,
    TicketStatus,
    TicketSubtype,
    TicketType,
)
from tests.conftest import FakeLinearPort, FakeSlackPort

# A fixed "today" so back-on dates are deterministic.
TODAY = "2026-09-29 12:00:00"


class _World:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        slack: FakeSlackPort,
        *,
        se_owner_user_ids: Collection[str] = ("U_SE", "U_ELIZA", "U_SAM"),
        default_se_owner_user_id: str | None = None,
        linear: LinearSync | None = None,
    ) -> None:
        self.tickets = SQLiteTicketRepository(factory)
        self.orgs = SQLiteOrgRepository(factory)
        self.repo = SQLiteSeAvailabilityRepository(factory)
        self.availability = SeAvailability(self.repo)
        events = SQLiteEventLogRepository(factory)
        self.submit = SubmitTicketForm(
            slack=slack,
            tickets=self.tickets,
            events=events,
            orgs=self.orgs,
            drafts=SQLiteDraftFormSessionRepository(factory),
            find_dedupe=FindDedupeCandidate(tickets=self.tickets),
            offer_dedupe=OfferDedupeChoice(
                slack=slack, pending=SQLitePendingDedupeChoiceRepository(factory)
            ),
            assign_priority=AssignPriority(matrix=PriorityMatrix(), events=events),
            se_user_id="U_SE",
            se_owner_user_ids=se_owner_user_ids,
            default_se_owner_user_id=default_se_owner_user_id,
            se_tickets_channel_id="C_SE_TICKETS",
            linear=linear,
            availability=self.availability,
        )
        self.mark_ooo = MarkOoo(
            repo=self.repo,
            availability=self.availability,
            tickets=self.tickets,
            apply_se_owner_change=ApplySeOwnerChange(
                tickets=self.tickets, slack=slack, orgs=self.orgs, linear=linear
            ),
            slack=slack,
            pick_owner=self.submit.pick_se_owner,
            se_tickets_channel_id="C_SE_TICKETS",
        )
        self.mark_back = MarkBack(repo=self.repo, slack=slack, se_tickets_channel_id="C_SE_TICKETS")

    async def log(self, summary: str, *, urgent: bool = False, csm_help: bool = False) -> Ticket:
        result = await self.submit.from_se_bug(
            SEBugSubmission(
                org_id="acme",
                source=Source.CUSTOMER_CHANNEL,
                summary=summary,
                description="",
                blocking=False,
                deadline=None,
                affected_user=None,
                replay_link=None,
                urgent=urgent,
                ticket_type=TicketType.CSM_HELP if csm_help else TicketType.BUG,
            ),
            reporter_user_id="U_OTHER",
            original_slack_link=f"link-{summary}",
        )
        assert result.ticket is not None
        return result.ticket

    async def seed_owned(self, owner: str, title: str, status: TicketStatus) -> Ticket:
        created = await self.tickets.create(
            Ticket(
                title=title,
                type=TicketType.BUG,
                subtype=TicketSubtype.PLATFORM_WIDE,
                severity=Severity.BLOCKING,
                lane=Lane.SE_ACTION,
                status=status,
                reporter_user_id="U_OTHER",
                se_owner_user_id=owner,
                source=Source.CUSTOMER_CHANNEL,
                description="",
                card_channel_id="C_SE_TICKETS",
                card_message_ts="1700000000.000100",
            )
        )
        assert created.id is not None
        await self.tickets.add_org(created.id, "acme")
        return created


@pytest.fixture
async def world(
    session_factory: async_sessionmaker[AsyncSession], fake_slack: FakeSlackPort
) -> _World:
    await SQLiteOrgRepository(session_factory).upsert(Org(id="acme", name="Acme Corp"))
    return _World(session_factory, fake_slack, default_se_owner_user_id="U_ELIZA")


@pytest.mark.asyncio
async def test_availability_repo_upsert_list_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SQLiteSeAvailabilityRepository(session_factory)
    await repo.upsert(SeAbsence(user_id="U_ELIZA", cover_user_id="U_SE", set_by_user_id="U_SE"))
    await repo.upsert(SeAbsence(user_id="U_ELIZA", back_on=date(2026, 10, 3)))  # overwrite
    [absence] = await repo.list_all()
    assert absence.cover_user_id is None
    assert absence.back_on == date(2026, 10, 3)
    assert await repo.delete("U_ELIZA") is True
    assert await repo.delete("U_ELIZA") is False
    assert await repo.list_all() == []


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_default_owner_out_routes_new_tickets_to_cover(
    world: _World, fake_slack: FakeSlackPort
) -> None:
    await world.mark_ooo.execute(
        user_id="U_ELIZA",
        cover_user_id="U_SAM",
        back_on=date(2026, 10, 3),
        move_open=False,
        by_user_id="U_SE",
    )

    assert (await world.log("Export greyed out")).se_owner_user_id == "U_SAM"
    assert (await world.log("On fire", urgent=True)).se_owner_user_id == "U_SAM"
    assert (await world.log("QBR deck", csm_help=True)).se_owner_user_id == "U_SAM"
    # Announced in #se-tickets, and the cover is told.
    notice = next(t for c, t, _ in fake_slack.messages_sent if c == "C_SE_TICKETS")
    assert "<@U_ELIZA> is out until Sat 3 Oct" in notice and "<@U_SAM>" in notice
    assert any(u == "U_SAM" and "covering <@U_ELIZA>" in t for u, t in fake_slack.dms_sent)


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_no_cover_round_robins_over_everyone_else(world: _World) -> None:
    await world.mark_ooo.execute(
        user_id="U_ELIZA", cover_user_id=None, back_on=None, move_open=False, by_user_id="U_ELIZA"
    )
    owners = [(await world.log(f"Bug {i}")).se_owner_user_id for i in range(4)]
    assert "U_ELIZA" not in owners
    assert sorted(owners) == ["U_SAM", "U_SAM", "U_SE", "U_SE"]


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_round_robin_skips_absentee_without_default_owner(
    session_factory: async_sessionmaker[AsyncSession], fake_slack: FakeSlackPort
) -> None:
    await SQLiteOrgRepository(session_factory).upsert(Org(id="acme", name="Acme Corp"))
    world = _World(session_factory, fake_slack)
    # A cover doesn't absorb the absentee's round-robin share — they're just skipped.
    await world.mark_ooo.execute(
        user_id="U_SE", cover_user_id="U_SAM", back_on=None, move_open=False, by_user_id="U_SE"
    )
    owners = [(await world.log(f"Bug {i}")).se_owner_user_id for i in range(4)]
    assert sorted(owners) == ["U_ELIZA", "U_ELIZA", "U_SAM", "U_SAM"]


@pytest.mark.asyncio
async def test_absence_expires_on_back_on_date(world: _World) -> None:
    with time_machine.travel("2026-09-29 12:00:00", tick=False):
        await world.mark_ooo.execute(
            user_id="U_ELIZA",
            cover_user_id="U_SAM",
            back_on=date(2026, 10, 1),
            move_open=False,
            by_user_id="U_SE",
        )
    with time_machine.travel("2026-09-30 23:00:00", tick=False):
        assert (await world.log("Still out")).se_owner_user_id == "U_SAM"
    with time_machine.travel("2026-10-01 08:00:00", tick=False):
        assert (await world.log("Back today")).se_owner_user_id == "U_ELIZA"


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_mark_back_restores_routing(world: _World, fake_slack: FakeSlackPort) -> None:
    await world.mark_ooo.execute(
        user_id="U_ELIZA", cover_user_id="U_SAM", back_on=None, move_open=False, by_user_id="U_SE"
    )
    assert await world.mark_back.execute(user_id="U_ELIZA", by_user_id="U_ELIZA") is True
    assert (await world.log("After return")).se_owner_user_id == "U_ELIZA"
    assert any("<@U_ELIZA> is back" in t for _c, t, _ in fake_slack.messages_sent)
    # Marking back someone who isn't out is a quiet no-op.
    assert await world.mark_back.execute(user_id="U_SAM", by_user_id="U_SAM") is False


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_cover_also_out_falls_back_to_round_robin(world: _World) -> None:
    await world.mark_ooo.execute(
        user_id="U_SAM", cover_user_id=None, back_on=None, move_open=False, by_user_id="U_SAM"
    )
    await world.mark_ooo.execute(
        user_id="U_ELIZA", cover_user_id="U_SAM", back_on=None, move_open=False, by_user_id="U_SE"
    )
    assert (await world.log("Who gets it")).se_owner_user_id == "U_SE"


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_everyone_out_keeps_original_owner(world: _World) -> None:
    for uid in ("U_SE", "U_ELIZA", "U_SAM"):
        await world.mark_ooo.execute(
            user_id=uid, cover_user_id=None, back_on=None, move_open=False, by_user_id=uid
        )
    assert (await world.log("Nobody home")).se_owner_user_id == "U_ELIZA"


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_move_open_moves_live_tickets_only(
    session_factory: async_sessionmaker[AsyncSession],
    fake_slack: FakeSlackPort,
    fake_linear: FakeLinearPort,
) -> None:
    await SQLiteOrgRepository(session_factory).upsert(Org(id="acme", name="Acme Corp"))
    tickets = SQLiteTicketRepository(session_factory)
    sync = LinearSync(
        linear=fake_linear, tickets=tickets, orgs=SQLiteOrgRepository(session_factory)
    )
    world = _World(session_factory, fake_slack, default_se_owner_user_id="U_ELIZA", linear=sync)
    new = await world.seed_owned("U_ELIZA", "New one", TicketStatus.NEW)
    wip = await world.seed_owned("U_ELIZA", "In progress", TicketStatus.IN_PROGRESS)
    done = await world.seed_owned("U_ELIZA", "Resolved", TicketStatus.RESOLVED)
    other = await world.seed_owned("U_SE", "Someone else's", TicketStatus.NEW)
    await sync.mirror_new_ticket(new)

    moved = await world.mark_ooo.execute(
        user_id="U_ELIZA", cover_user_id="U_SAM", back_on=None, move_open=True, by_user_id="U_SE"
    )

    assert moved == 2
    owners = {}
    for t in (new, wip, done, other):
        persisted = await tickets.get(t.id or 0)
        assert persisted is not None
        owners[t.title] = persisted.se_owner_user_id
    assert owners == {
        "New one": "U_SAM",
        "In progress": "U_SAM",
        "Resolved": "U_ELIZA",
        "Someone else's": "U_SE",
    }
    assert fake_slack.messages_updated  # cards redrawn
    assert ("lin_1", "U_SAM") in fake_linear.assignments  # Linear assignee synced
    assert any("Moved 2 open tickets" in t for _c, t, _ in fake_slack.messages_sent)


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_move_open_without_cover_spreads_across_pool(world: _World) -> None:
    for i in range(4):
        await world.seed_owned("U_ELIZA", f"Open {i}", TicketStatus.NEW)
    moved = await world.mark_ooo.execute(
        user_id="U_ELIZA", cover_user_id=None, back_on=None, move_open=True, by_user_id="U_ELIZA"
    )
    assert moved == 4
    owners = [t.se_owner_user_id for t in await world.tickets.query_live()]
    assert sorted(owners) == ["U_SAM", "U_SAM", "U_SE", "U_SE"]


@pytest.mark.asyncio
@time_machine.travel(TODAY, tick=False)
async def test_validate(world: _World) -> None:
    await world.mark_ooo.execute(
        user_id="U_SAM", cover_user_id=None, back_on=None, move_open=False, by_user_id="U_SAM"
    )

    async def fields(**kw: object) -> list[str]:
        errors = await world.mark_ooo.validate(user_id="U_ELIZA", **kw)  # type: ignore[arg-type]
        return [e.field for e in errors]

    assert await fields(cover_user_id="U_SE", back_on=date(2026, 9, 30)) == []
    assert await fields(cover_user_id=None, back_on=date(2026, 9, 29)) == ["back_on"]
    assert await fields(cover_user_id="U_ELIZA", back_on=None) == ["cover"]
    assert await fields(cover_user_id="U_SAM", back_on=None) == ["cover"]
