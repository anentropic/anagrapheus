"""The belief fold: beliefs and the index both rebuilt from the log, however it was folded."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.beliefs import (
    FOLD_NAME,
    BeliefFold,
    Contract,
    Edge,
    Revise,
    StateRef,
    UnresolvedStateRef,
)
from anagrapheus.timeline import DomainEvent, EventTypeRegistry

from .conftest import Rig, beliefs_of, build_predicates, build_rig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from anagrapheus.beliefs import BeliefOp
    from anagrapheus.timeline import Envelope, PendingEvent


def test_the_fold_ignores_events_no_interpreter_claims(rig: Rig):
    """The fold shares the stream with the whole domain; most of it is none of its business."""

    class Shipped(DomainEvent, frozen=True):
        order: str

    rig.types.event("shop", "Shipped")(Shipped)
    entity = uuid.uuid7()

    rig.commit(rig.asserted("location", entity), rig.types.pending(Shipped(order="a-1")))

    assert [row.predicate for row in rig.index.beliefs_for(entity)] == ["location"]


# -- the application's own events ------------------------------------------------------


class Observed(DomainEvent, frozen=True):
    """An application's observation event: one witness sees one person somewhere."""

    witness: str
    person: uuid.UUID
    place: str


class Recanted(DomainEvent, frozen=True):
    """The witness takes it back."""

    witness: str
    person: uuid.UUID


def _observed(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    assert isinstance(event, Observed)
    return (Revise(f"witness:{event.witness}", "location", (event.person,), event.place),)


def _recanted(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    assert isinstance(event, Recanted)
    return (Contract(f"witness:{event.witness}", "location", (event.person,)),)


def _app_rig(path: Path) -> Rig:
    types = EventTypeRegistry()
    from anagrapheus import register_builtin_events

    register_builtin_events(types)
    types.event("app", "Observed")(Observed)
    types.event("app", "Recanted")(Recanted)
    return build_rig(
        path,
        types=types,
        interpret={("app", "Observed"): _observed, ("app", "Recanted"): _recanted},
    )


def test_an_application_event_folds_to_beliefs_without_a_library_event(tmp_path: Path):
    """
    The library ships the shape; the application owns the name.

    Doc 00 §8's rule, applied to belief. An observation event *is* the belief's cause
    and its content, so the application binds it to an interpreter and never emits a
    ``BeliefRevised`` beside it. This is the shape NVM's belief ingestion has: beliefs
    derived from observation events, the log the only place they live.
    """
    rig = _app_rig(tmp_path / "app.db")
    try:
        alice = uuid.uuid7()
        seen = rig.types.pending(Observed(witness="bob", person=alice, place="London"))
        rig.commit(seen)

        [state] = rig.core.query_scope("witness:bob", BeliefFilter())
        assert state.value == "London"
        # The state's source *is* the observation, because that is the event folded.
        assert state.source_event_id == seen.envelope.event_id
        assert [r.predicate for r in rig.index.beliefs_for(alice)] == ["location"]

        rig.commit(rig.types.pending(Recanted(witness="bob", person=alice)))
        assert rig.core.query_scope("witness:bob", BeliefFilter()) == []

        before = beliefs_of(rig.core, ("witness:bob",))
        rig.rebuild()
        assert beliefs_of(rig.core, ("witness:bob",)) == before
    finally:
        rig.store.close()


def test_an_interpreter_that_writes_one_belief_twice_is_refused(tmp_path: Path):
    """
    One event, one write per proposition per scope.

    A state's source is the event it was folded from, so a second write of the same
    belief by the same event would be indistinguishable from a replay of the first and
    silently skipped. Refused instead, loudly.
    """

    def twice(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
        assert isinstance(event, Observed)
        op = Revise("w", "location", (event.person,), event.place)
        return (op, op)

    types = EventTypeRegistry()
    types.event("app", "Observed")(Observed)
    rig = build_rig(tmp_path / "twice.db", types=types, interpret={("app", "Observed"): twice})
    try:
        rig.append(rig.types.pending(Observed(witness="w", person=uuid.uuid7(), place="x")))
        with pytest.raises(ValueError, match="twice"):
            rig.projector.catch_up()
    finally:
        rig.store.close()


# -- idempotence, edges, reset ---------------------------------------------------------


def test_re_applying_a_batch_changes_nothing(rig: Rig):
    """
    The fold contract allows the last batch to be re-applied after a crash.

    doxastica is not in the log's transaction, so this is the case that matters: a
    crash after the store was written and before the position committed. The fold
    recognises its own writes by their source event and skips them.
    """
    entity = uuid.uuid7()
    rig.commit(rig.events.revise("agent", "location", (entity,), "London"))
    index_before = _index(rig)
    beliefs_before = beliefs_of(rig.core, ("agent",))

    rig.fold.apply(next(iter(rig.store.read())), rig.store.connection)

    assert _index(rig) == index_before
    assert beliefs_of(rig.core, ("agent",)) == beliefs_before


def test_minting_the_same_proposition_twice_is_one_index_row(rig: Rig):
    """The same claim asserted in two scopes is one id, and one row."""
    entity = uuid.uuid7()
    rig.commit(rig.asserted("location", entity), rig.asserted("location", entity, scope="other"))

    assert len(rig.index.beliefs_for(entity)) == 1


def test_an_edge_survives_a_rebuild(rig: Rig):
    """
    The dependency is in the log, so a rebuilt store still knows what rests on what.

    Laid on the store directly — as the demo did until note 03 §9 — the edge would be
    gone after a rebuild, and retracting the premise would stop reporting the
    conclusion that rests on it.
    """
    alice = uuid.uuid7()
    believer = rig.believer("agent")
    premise = believer.revise("location", (alice,), "London")
    believer.for_scope("analyst").revise("employer", (alice,), "BBC", rests_on=premise)

    rig.rebuild()

    [premise_now] = rig.core.query_scope("agent", BeliefFilter())
    reached = rig.core.get_impact(premise_now.state_id).reached
    assert [(s.scope_id, s.value) for s in reached] == [("analyst", "BBC")]


def test_a_crash_between_a_state_and_its_edge_loses_neither(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
):
    """
    The state is in doxastica and the position rolled back, so the retry must lay the edge.

    Skipping a state already folded is not enough: the crash can fall after the state
    was written and before its edges were, and a retry that skipped the whole operation
    would commit the position with the dependency lost for good.
    """
    alice = uuid.uuid7()
    premise = rig.believer("agent").revise("location", (alice,), "London")
    rig.append(
        rig.events.revise(
            "analyst", "employer", (alice,), "BBC", edges=(Edge(StateRef.of(premise)),)
        )
    )

    def crash(*_args: object) -> None:
        raise RuntimeError("the process died here")

    with monkeypatch.context() as patch:
        patch.setattr(rig.core, "add_edge", crash)
        with pytest.raises(RuntimeError):
            rig.projector.catch_up()
    assert len(rig.core.query_scope("analyst", BeliefFilter())) == 1, "the state got in"

    rig.projector.catch_up()

    assert len(rig.core.query_scope("analyst", BeliefFilter())) == 1, "and only once"
    reached = rig.core.get_impact(premise.state_id).reached
    assert [(s.scope_id, s.value) for s in reached] == [("analyst", "BBC")]


def test_an_unresolved_edge_refuses_the_operation_before_writing_it(rig: Rig):
    """Targets resolve first, so a refused operation leaves no state behind without its edge."""
    ghost = StateRef("agent", rig.predicates.belief_id("location", uuid.uuid7()), uuid.uuid7())
    rig.append(rig.events.revise("agent", "location", (uuid.uuid7(),), "x", edges=(Edge(ghost),)))

    with pytest.raises(UnresolvedStateRef):
        rig.projector.catch_up()
    assert rig.core.query_scope("agent", BeliefFilter()) == []


def test_an_edge_to_a_state_the_log_does_not_hold_is_refused(rig: Rig):
    """The store would lay nothing and say nothing; the fold says so."""
    ghost = StateRef("agent", rig.predicates.belief_id("location", uuid.uuid7()), uuid.uuid7())
    rig.append(rig.events.revise("agent", "location", (uuid.uuid7(),), "x", edges=(Edge(ghost),)))

    with pytest.raises(UnresolvedStateRef):
        rig.projector.catch_up()


def test_reset_folds_into_the_store_it_is_given(rig: Rig):
    """There is no "clear" on a belief store; the caller hands the fold an empty one."""
    rig.commit(rig.asserted("location", uuid.uuid7()))
    old = rig.core
    fresh = MemoryCore(InMemoryBackend())

    rig.fold.reset(fresh)

    assert rig.core is fresh
    assert rig.core.query_scope("agent", BeliefFilter()) == []
    assert old.query_scope("agent", BeliefFilter()) != [], "the old store is untouched"


# -- the invariant ---------------------------------------------------------------------

_OPS = st.sampled_from(["revise", "expand", "contract"])
_STANCES = st.sampled_from(list(Stance))


@settings(deadline=None, max_examples=40)
@given(
    plan=st.lists(
        st.tuples(
            _OPS,
            st.sampled_from(["agent", "rumour"]),
            st.sampled_from(["location", "employer", "knows", "sibling"]),
            _STANCES,
        ),
        min_size=1,
        max_size=10,
    ),
    cut=st.integers(min_value=0, max_value=10),
    batch_size=st.integers(min_value=1, max_value=4),
)
def test_straight_through_equals_interrupted_equals_rebuilt(
    tmp_path_factory: pytest.TempPathFactory,
    plan: list[tuple[str, str, str, Stance]],
    cut: int,
    batch_size: int,
):
    """
    Fold-replay is an invariant: same log, same beliefs and same index, however folded.

    The M1 and M2 property, applied now to what is *believed* and not only to which ids
    were minted — the gap note 03 §9 closed. ``state_id`` aside, which doxastica mints,
    every revision, value, status and stance comes back.
    """
    predicates = build_predicates()
    # A small pool of entities, so revisions and contractions actually collide.
    pool = [uuid.UUID(int=n) for n in range(1, 4)]
    root = tmp_path_factory.mktemp("replay")

    def events(rig: Rig) -> list[PendingEvent]:
        built: list[PendingEvent] = []
        for i, (op, scope, name, stance) in enumerate(plan):
            arity = predicates.predicate(name).arity
            ids = tuple(pool[(i + k) % len(pool)] for k in range(arity))
            relational = name in {"knows", "sibling"}
            value: object = True if relational else f"v{i % 2}"
            if op == "contract":
                built.append(rig.events.contract(scope, name, ids))
            elif op == "expand":
                built.append(rig.events.expand(scope, name, ids, value, stance=stance))
            else:
                built.append(rig.events.revise(scope, name, ids, value, stance=stance))
        return built

    straight = build_rig(root / "straight.db")
    try:
        straight.commit(*events(straight))
        expected = (_index(straight), beliefs_of(straight.core, ("agent", "rumour")))
    finally:
        straight.store.close()

    interrupted = build_rig(root / "interrupted.db")
    try:
        pending = events(interrupted)
        head, tail = pending[: cut or None], pending[cut:] if cut else []
        interrupted.append(*head)
        interrupted.projector.catch_up(batch_size=batch_size)
        if tail:
            interrupted.append(*tail)
            interrupted.projector.catch_up(batch_size=batch_size)
        got = (_index(interrupted), beliefs_of(interrupted.core, ("agent", "rumour")))
        assert _comparable(got) == _comparable(expected)

        interrupted.rebuild()
        rebuilt = (_index(interrupted), beliefs_of(interrupted.core, ("agent", "rumour")))
        assert rebuilt == got
    finally:
        interrupted.store.close()


def _comparable(snapshot: tuple[object, list[tuple[object, ...]]]) -> object:
    # Two logs built from one plan carry different event ids, so a source is compared by
    # its position in the scope's history rather than by value.
    index, beliefs = snapshot
    return index, [row[:2] + row[3:] for row in beliefs]


def _index(rig: Rig) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: sorted(tuple(row) for row in rig.store.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in ("belief_ids", "belief_entities")
    }


# -- where the position lives ----------------------------------------------------------


def test_a_reopened_non_durable_fold_folds_the_whole_log_again(tmp_path: Path):
    """
    The beliefs went with the process, so the position must have gone with them.

    Reopening hands the fold an empty in-memory store over a file whose entity index
    survived. Catch-up replays everything over that index, which upserts, so the store
    and the index both come back agreeing with the log, with no rebuild.
    """
    path = tmp_path / "reopen.db"
    alice, bob = uuid.uuid7(), uuid.uuid7()
    first = build_rig(path)
    try:
        first.commit(
            first.events.revise("agent", "location", (alice,), "London"),
            first.events.revise("agent", "knows", (alice, bob), True),
            first.events.revise("rumour", "location", (alice,), "Paris"),
        )
        first.commit(first.events.contract("rumour", "location", (alice,)))
        expected = (_index(first), beliefs_of(first.core, ("agent", "rumour")))
    finally:
        first.store.close()

    second = build_rig(path)
    try:
        assert second.projector.position(FOLD_NAME) == 0
        assert second.core.query_scope("agent", BeliefFilter()) == []

        second.projector.catch_up()

        assert second.projector.position(FOLD_NAME) == second.store.last_seq()
        assert (_index(second), beliefs_of(second.core, ("agent", "rumour"))) == expected
        # Never written to the file, so a later reopen cannot be misled by it either.
        assert second.store.read_position(FOLD_NAME) == 0
    finally:
        second.store.close()


def test_a_durable_fold_keeps_its_position_in_the_file(tmp_path: Path):
    """
    A store that outlives the process resumes where it stopped, and folds nothing twice.

    One ``MemoryCore`` handed to both rigs stands in for a database that survived.
    """
    path = tmp_path / "durable.db"
    core = MemoryCore(InMemoryBackend())
    entity = uuid.uuid7()
    first = build_rig(path, core=core, durable=True)
    try:
        last = first.commit(first.events.revise("agent", "location", (entity,), "London"))
        assert first.store.read_position(FOLD_NAME) == last
    finally:
        first.store.close()

    second = build_rig(path, core=core, durable=True)
    try:
        assert second.projector.position(FOLD_NAME) == last
        before = beliefs_of(core, ("agent",))

        second.projector.catch_up()
        assert beliefs_of(core, ("agent",)) == before

        later = second.commit(second.events.revise("agent", "location", (entity,), "Paris"))
        assert second.store.read_position(FOLD_NAME) == later
        belief_id = second.predicates.belief_id("location", entity)
        assert [s.value for s in core.get_revision_chain(belief_id)] == ["London", "Paris"]
    finally:
        second.store.close()


def test_a_durable_fold_refuses_to_hold_a_position(rig: Rig):
    """Its position is the file's; one kept on the object as well could disagree with it."""
    durable = BeliefFold(rig.core, rig.predicates, rig.index_store, rig.types, durable=True)
    assert durable.read_position() is None
    with pytest.raises(TypeError, match="durable"):
        durable.commit_position(1)
