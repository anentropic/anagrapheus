"""Building decisions, and the replay hazard in minting an entity id."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from anagrapheus.identity import (
    ENTITY_PIN_KIND,
    Established,
    Stance,
    alias,
    establish,
    merge,
    mint_entity_id,
    register_builtin_events,
    split,
)
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
)
from anagrapheus.timeline import register_builtin_events as register_pin_events
from anagrapheus.timeline.registry import RESERVED_NAMESPACE

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline import PinResolver


@pytest.fixture
def types() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    register_builtin_events(registry)
    register_pin_events(registry)
    return registry


def test_establish_returns_the_id_it_minted(types: EventTypeRegistry):
    entity_id, event = establish(types, "alice@corp", provenance="signup")
    assert event.envelope.namespace == RESERVED_NAMESPACE
    assert event.payload["entity_id"] == str(entity_id)
    assert event.payload["first_surface_form"] == "alice@corp"


def test_establish_accepts_an_id_you_already_hold(types: EventTypeRegistry):
    given = UUID(int=42)
    entity_id, _ = establish(types, "alice", provenance="x", entity_id=given)
    assert entity_id == given


def test_decisions_carry_a_correlation_id(types: EventTypeRegistry):
    run = uuid.uuid7()
    _, event = establish(types, "alice", provenance="x", correlation_id=run)
    assert event.envelope.correlation_id == run
    assert (
        merge(
            types, kept=UUID(int=1), absorbed=UUID(int=2), correlation_id=run
        ).envelope.correlation_id
        == run
    )


def test_alias_and_split_serialize_their_shapes(types: EventTypeRegistry):
    entity_id = UUID(int=1)
    aliased = alias(types, entity_id, "Alice", Stance.SUSPECTED)
    assert aliased.payload["stance"] == "suspected"

    carved = UUID(int=2)
    carved_event = split(types, entity_id, [(carved, ["Alice"])])
    assert carved_event.payload["into"] == [{"entity_id": str(carved), "surface_forms": ["Alice"]}]


def test_unpinned_mint_draws_a_fresh_id_every_time():
    material = {"surface_form": "alice@corp"}
    assert mint_entity_id(material) != mint_entity_id(material)


def _establish_under(resolver: PinResolver) -> UUID:
    """One scripted unit of work: mint an entity id through whatever resolver."""
    return mint_entity_id({"surface_form": "alice@corp", "provenance": "signup"}, resolver=resolver)


def test_a_pinned_mint_replays_to_the_same_entity(tmp_path: Path, types: EventTypeRegistry):
    """
    The reason minting is pinnable at all.

    Establishing consumes entropy, so a generation-replay of an unpinned mint would
    invent a second entity and orphan everything keyed on the first.
    """
    store = SQLiteEventStore(tmp_path / "live.db")
    try:
        ledger = PinLedger(store, types)
        live = _establish_under(ledger)
        ledger.commit()
        recorded = list(store.read())
    finally:
        store.close()

    replay = ReplayPins.from_events(recorded, types, on_miss="strict")
    assert _establish_under(replay) == live


def test_an_unpinned_mint_does_not_agree_with_the_original(
    tmp_path: Path, types: EventTypeRegistry
):
    """The caveat, asserted rather than assumed."""
    store = SQLiteEventStore(tmp_path / "live.db")
    try:
        ledger = PinLedger(store, types)
        live = _establish_under(ledger)
        ledger.commit()
    finally:
        store.close()

    assert mint_entity_id({"surface_form": "alice@corp", "provenance": "signup"}) != live


def test_the_pin_key_is_scoped_to_entities(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "live.db")
    try:
        ledger = PinLedger(store, types)
        _establish_under(ledger)
        ledger.commit()
        promoted = [e for e in store.read() if e.envelope.type == "PinPromoted"]
    finally:
        store.close()

    assert len(promoted) == 1
    assert promoted[0].payload["kind"] == ENTITY_PIN_KIND


def test_different_material_mints_a_different_entity(tmp_path: Path, types: EventTypeRegistry):
    """Two distinct entities established in one unit of work must not share an id."""
    store = SQLiteEventStore(tmp_path / "live.db")
    try:
        ledger = PinLedger(store, types)
        first = mint_entity_id({"surface_form": "alice@corp"}, resolver=ledger)
        second = mint_entity_id({"surface_form": "bob@corp"}, resolver=ledger)
    finally:
        store.close()

    assert first != second


def test_establish_threads_the_resolver_through(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "live.db")
    try:
        ledger = PinLedger(store, types)
        entity_id, event = establish(types, "alice@corp", provenance="signup", resolver=ledger)
        ledger.commit()
        recorded = list(store.read())
    finally:
        store.close()

    replayed, _ = establish(
        types,
        "alice@corp",
        provenance="signup",
        resolver=ReplayPins.from_events(recorded, types, on_miss="strict"),
    )
    assert replayed == entity_id
    assert Established.model_validate(event.payload).entity_id == entity_id
