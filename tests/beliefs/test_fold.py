"""The belief fold: dispatch, indifference to foreign events, and replay invariance."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.beliefs import (
    BeliefIndexFold,
    BeliefMinted,
    BeliefRole,
    BeliefVocabulary,
    RoleBinding,
    mint,
)
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    Projector,
    UpcastRegistry,
)

from .conftest import Rig, build_predicates, build_rig

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_the_fold_ignores_events_that_are_not_mintings(rig: Rig):
    """The index shares the stream with the whole domain; most of it is none of its business."""

    class Shipped(DomainEvent, frozen=True):
        order: str

    rig.types.event("shop", "Shipped")(Shipped)
    entity = uuid.uuid7()
    _, minted = mint(rig.types, rig.predicates, "location", entity)

    rig.commit(minted, rig.types.pending(Shipped(order="a-1")))

    assert [row.predicate for row in rig.index.beliefs_for(entity)] == ["location"]


def test_an_application_vocabulary_folds_to_the_same_index(tmp_path: Path):
    """
    The library ships the shape; the application owns the name.

    §8's rule, applied a third time. A consumer that already emits its own minting
    event binds that type and never sees a library one.
    """

    class ClaimCoined(DomainEvent, frozen=True):
        belief_id: str
        predicate: str
        entity_ids: tuple[uuid.UUID, ...]

    def adapt(event: DomainEvent) -> BeliefMinted:
        assert isinstance(event, ClaimCoined)
        return BeliefMinted(
            belief_id=event.belief_id,
            predicate=event.predicate,
            entity_ids=event.entity_ids,
        )

    types = EventTypeRegistry()
    types.event("nvm", "ClaimCoined")(ClaimCoined)
    vocabulary = BeliefVocabulary({BeliefRole.MINTED: RoleBinding("nvm", "ClaimCoined", adapt)})

    predicates = build_predicates()
    entity = uuid.uuid7()
    belief_id = predicates.belief_id("location", entity)

    native = build_rig(tmp_path / "native.db")
    try:
        native.commit(mint(native.types, predicates, "location", entity)[1])
        expected = [
            (r.belief_id, r.predicate, r.entity_ids) for r in native.index.beliefs_for(entity)
        ]
    finally:
        native.store.close()

    foreign = build_rig(tmp_path / "foreign.db", types=types)
    foreign.fold = BeliefIndexFold(foreign.index_store, types, vocabulary=vocabulary)
    foreign.projector = Projector(
        foreign.store, EventReader(foreign.store, UpcastRegistry(), types), [foreign.fold]
    )
    try:
        foreign.commit(
            types.pending(
                ClaimCoined(belief_id=belief_id, predicate="location", entity_ids=(entity,))
            )
        )
        actual = [
            (r.belief_id, r.predicate, r.entity_ids) for r in foreign.index.beliefs_for(entity)
        ]
    finally:
        foreign.store.close()

    assert actual == expected


def test_re_applying_a_batch_changes_nothing(rig: Rig):
    """The fold contract allows the last batch to be re-applied after a crash."""
    entity = uuid.uuid7()
    _, minted = mint(rig.types, rig.predicates, "location", entity)
    rig.commit(minted)
    before = _snapshot(rig)

    rig.fold.apply(next(iter(rig.store.read())), rig.store.connection)

    assert _snapshot(rig) == before


def test_minting_the_same_proposition_twice_is_one_row(rig: Rig):
    """
    Minting the same proposition twice is ordinary rather than an error.

    Ordinary rather than an error: the same claim can be asserted in two scopes, or
    re-asserted later, and minting is deterministic so both produce one id.
    """
    entity = uuid.uuid7()
    first, e1 = mint(rig.types, rig.predicates, "location", entity)
    second, e2 = mint(rig.types, rig.predicates, "location", entity)
    rig.commit(e1, e2)

    assert first == second
    assert len(rig.index.beliefs_for(entity)) == 1


_UUIDS = st.builds(uuid.UUID, st.binary(min_size=16, max_size=16).map(bytes.hex))


@settings(deadline=None, max_examples=40)
@given(
    plan=st.lists(
        st.tuples(st.sampled_from(["location", "employer", "knows", "sibling"]), _UUIDS, _UUIDS),
        min_size=1,
        max_size=8,
    ),
    cut=st.integers(min_value=0, max_value=8),
    batch_size=st.integers(min_value=1, max_value=4),
)
def test_straight_through_equals_interrupted_equals_rebuilt(
    tmp_path_factory: pytest.TempPathFactory,
    plan: list[tuple[str, uuid.UUID, uuid.UUID]],
    cut: int,
    batch_size: int,
):
    """
    Fold-replay is an invariant: same log, same index, however the folding was cut up.

    The M1 and M2 property, applied to the third projection in the stack.
    """
    predicates = build_predicates()
    events: list[tuple[str, tuple[uuid.UUID, ...]]] = []
    for name, a, b in plan:
        arity = predicates.predicate(name).arity
        events.append((name, (a,) if arity == 1 else (a, b)))

    root = tmp_path_factory.mktemp("replay")

    straight = build_rig(root / "straight.db")
    try:
        straight.commit(*[mint(straight.types, predicates, n, *e)[1] for n, e in events])
        expected = _snapshot(straight)
    finally:
        straight.store.close()

    interrupted = build_rig(root / "interrupted.db")
    try:
        pending = [mint(interrupted.types, predicates, n, *e)[1] for n, e in events]
        head, tail = pending[: cut or None], pending[cut:] if cut else []
        interrupted.append(*head)
        interrupted.projector.catch_up(batch_size=batch_size)
        if tail:
            interrupted.append(*tail)
            interrupted.projector.catch_up(batch_size=batch_size)
        assert _snapshot(interrupted) == expected

        interrupted.rebuild()
        assert _snapshot(interrupted) == expected
    finally:
        interrupted.store.close()


def _snapshot(rig: Rig) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: sorted(tuple(row) for row in rig.store.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in ("belief_ids", "belief_entities")
    }
