"""Which events are the pin machinery's own records, and which are a run's content."""

from __future__ import annotations

import uuid

import pytest

from anagrapheus import register_builtin_events
from anagrapheus.beliefs.events import BUILTIN_INTERPRETERS
from anagrapheus.identity.vocabulary import IdentityRole
from anagrapheus.timeline import (
    DiscardedPin,
    DomainEvent,
    Envelope,
    EventTypeRegistry,
    PinPromoted,
    PinsAbandoned,
    ReplayDiverged,
    is_bookkeeping,
)
from anagrapheus.timeline.registry import RESERVED_NAMESPACE


class StepTaken(DomainEvent, frozen=True):
    what: str


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    register_builtin_events(reg)
    reg.event("app", "StepTaken")(StepTaken)
    return reg


@pytest.mark.parametrize(
    "event",
    [
        PinPromoted(unit_id=uuid.uuid7(), key="k", kind="draw", result=1, attempts=1),
        PinsAbandoned(
            unit_id=uuid.uuid7(),
            reason="gave up",
            discarded=[DiscardedPin(key="k", kind="draw", attempts=2)],
        ),
        ReplayDiverged(unit_id=uuid.uuid7(), missed_key="k", kind="draw"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_every_event_the_pin_machinery_appends_is_bookkeeping(
    types: EventTypeRegistry, event: DomainEvent
):
    assert is_bookkeeping(types.pending(event).envelope)


def test_an_application_event_is_not_bookkeeping(types: EventTypeRegistry):
    assert not is_bookkeeping(types.pending(StepTaken(what="a")).envelope)


@pytest.mark.parametrize(
    "name",
    [*(role.value for role in IdentityRole), *(t for _, t in BUILTIN_INTERPRETERS)],
)
def test_the_rest_of_the_reserved_namespace_is_content(types: EventTypeRegistry, name: str):
    """Library events that record what a run did, such as minted beliefs, are kept."""
    assert types.latest_version(RESERVED_NAMESPACE, name) == 1, "a real library event"
    envelope = Envelope(namespace=RESERVED_NAMESPACE, type=name, schema_version=1)

    assert not is_bookkeeping(envelope)
