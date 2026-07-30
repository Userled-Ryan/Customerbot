from __future__ import annotations

import pytest

from customerbot.domain.tickets.value_objects import (
    TicketSubtype,
    TicketType,
    subtypes_for,
)


@pytest.mark.parametrize("ticket_type", list(TicketType))
def test_every_type_has_at_least_one_subtype(ticket_type: TicketType) -> None:
    """`subtypes_for` must resolve for every declared type — a new `TicketType`
    without a `_SUBTYPES_BY_TYPE` entry would `KeyError` at intake."""
    subtypes = subtypes_for(ticket_type)
    assert subtypes, f"{ticket_type} has no subtypes"


def test_csm_help_has_single_general_assistance_subtype() -> None:
    assert subtypes_for(TicketType.CSM_HELP) == (TicketSubtype.CSM_ASSISTANCE,)
