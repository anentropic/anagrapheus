"""
Milestone-2 acceptance: the identity layer, end to end, one story.

An agent ingests two contact records that turn out to be one person, and one company
record that turns out to be two. Establish -> alias -> resolve ambiguously -> merge ->
canonical follows -> the belief layer's re-key seam yields the consequence -> split ->
crash and resume above the position -> rebuild equals incremental.

The entity ids are minted through the pin ledger throughout, so the whole story is
replayable: that is what makes identity safe to establish inside a run that may be
re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from anagrapheus.identity import (
    AmbiguousSurfaceForm,
    IdentityFold,
    IdentityRegistry,
    Ladder,
    SQLiteIdentityStore,
    alias,
    establish,
    merge,
    mint_entity_id,
    register_builtin_events,
    split,
)
from anagrapheus.identity.fold import FOLD_NAME
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    PinLedger,
    Projector,
    ReplayPins,
    SQLiteEventStore,
    UpcastRegistry,
)
from anagrapheus.timeline import register_builtin_events as register_pin_events

if TYPE_CHECKING:
    from pathlib import Path

# The consuming application's own stance vocabulary — here, R21's ladder.
LADDER = Ladder(("doubted", "suspected", "believed", "certain"))


def make_types() -> EventTypeRegistry:
    types = EventTypeRegistry()
    register_builtin_events(types)
    register_pin_events(types)
    return types


def open_stack(path: Path, types: EventTypeRegistry):
    """Everything wired over one file, as a composition root would do it."""
    store = SQLiteEventStore(path)
    identity_store = SQLiteIdentityStore(store.connection)
    fold = IdentityFold(identity_store, types)
    projector = Projector(store, EventReader(store, UpcastRegistry(), types), [fold])
    return store, identity_store, projector, IdentityRegistry(identity_store, ladder=LADDER)


def test_milestone_2_acceptance(tmp_path: Path):
    path = tmp_path / "agent.db"
    types = make_types()
    store, identity_store, projector, registry = open_stack(path, types)

    # -- the agent ingests three records, minting every id through the pin ledger ----
    ledger = PinLedger(store, types)

    contact_a, e_a = establish(types, "alice@corp", provenance="crm", resolver=ledger)
    contact_b, e_b = establish(types, "Alice Smith", provenance="email", resolver=ledger)
    acme, e_acme = establish(types, "Acme", provenance="crm", resolver=ledger)
    store.append([e_a, e_b, e_acme])

    # a human (or a model) offers another name for the second contact, hedged
    store.append([alias(types, contact_b, "A. Smith", "suspected")])
    projector.catch_up()

    assert registry.resolve("alice@corp").sole() == contact_a
    assert registry.resolve("A. Smith").sole() == contact_b

    # -- the same form now points at two entities: ambiguous, and the layer says so --
    store.append([alias(types, contact_a, "A. Smith", "believed")])
    projector.catch_up()

    ambiguous = registry.resolve("A. Smith")
    assert {c.entity_id for c in ambiguous.candidates} == {contact_a, contact_b}
    assert [c.stance for c in ambiguous.candidates] == ["believed", "suspected"]
    with pytest.raises(AmbiguousSurfaceForm):
        ambiguous.sole()

    # -- something judges them one person; the decision is an event ------------------
    before_merge = store.last_seq()
    store.append([merge(types, kept=contact_a, absorbed=contact_b)])
    projector.catch_up()

    assert registry.resolve("A. Smith").sole() == contact_a
    assert registry.canonical(contact_b) == contact_a

    # -- and the belief layer learns exactly what it must re-key ---------------------
    rekeys = registry.rekeys_since(before_merge)
    assert [(r.kind, r.from_entity, r.to_entity) for r in rekeys] == [
        ("merge", contact_b, contact_a)
    ]

    # -- the company turns out to be two; the original survives the split ------------
    before_split = store.last_seq()
    acme_uk = mint_entity_id({"surface_form": "Acme UK"}, resolver=ledger)
    store.append(
        [
            alias(types, acme, "Acme UK", "believed"),
            split(types, acme, [(acme_uk, ["Acme UK"])]),
        ]
    )
    projector.catch_up()

    assert registry.resolve("Acme UK").sole() == acme_uk
    assert registry.resolve("Acme").sole() == acme
    assert registry.canonical(acme) == acme

    # a split names every product, because which one a belief belongs to is not
    # this layer's judgment to make
    split_rekeys = registry.rekeys_since(before_split)
    assert [(r.kind, r.from_entity, r.to_entity) for r in split_rekeys] == [
        ("split", acme, acme_uk)
    ]

    # -- the audit trail is the whole point of making decisions eventful -------------
    assert [row.kind for row in registry.history(contact_b)] == [
        "established",
        "aliased",
        "absorbed",
    ]

    # -- crash: a fresh process resumes above the position, folding nothing twice ----
    ledger.commit()
    position_before = store.read_position(FOLD_NAME)
    store.close()

    store, identity_store, projector, registry = open_stack(path, make_types())
    assert store.read_position(FOLD_NAME) == position_before
    projector.catch_up()
    incremental = _snapshot(store)

    # -- rebuild: same log, same registry -------------------------------------------
    projector.rebuild(FOLD_NAME, reset=identity_store.reset, reset_in_transaction=True)
    assert _snapshot(store) == incremental
    assert registry.resolve("A. Smith").sole() == contact_a
    assert registry.canonical(contact_b) == contact_a
    store.close()

    # -- and the run replays: the pinned mints hand back the same entities -----------
    recorded = SQLiteEventStore(path)
    try:
        events = list(recorded.read())
    finally:
        recorded.close()
    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed_a, _ = establish(types, "alice@corp", provenance="crm", resolver=replay)
    assert replayed_a == contact_a


def _snapshot(store: SQLiteEventStore) -> dict[str, list[tuple[object, ...]]]:
    tables = ("identity_entities", "identity_aliases", "identity_decisions", "identity_rekeys")
    return {
        table: sorted(tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in tables
    }
