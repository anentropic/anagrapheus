"""Wiring shared by the belief suite: one file, one connection, one projector, one fold."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance, Status

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    FOLD_NAME,
    BeliefEvents,
    BeliefFold,
    BeliefIndex,
    Edge,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    SQLiteBeliefIndexStore,
    StateRef,
)
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path
    from uuid import UUID

    from doxastica import BeliefState

    from anagrapheus.beliefs import Interpreter
    from anagrapheus.timeline import PendingEvent

# A test application's own predicates, declared exactly as a consumer would: in code.
PREDICATES = (
    Predicate("location", 1, PredicateKind.FUNCTIONAL),
    Predicate("employer", 1, PredicateKind.FUNCTIONAL),
    Predicate("tier", 1, PredicateKind.FUNCTIONAL, values=frozenset({"free", "pro"})),
    Predicate("knows", 2, PredicateKind.RELATIONAL),
    Predicate("sibling", 2, PredicateKind.RELATIONAL, ordered=False),
)


def build_predicates(namespace: str = "crm") -> PredicateRegistry:
    return PredicateRegistry(PREDICATES, namespace=namespace)


@dataclass
class Rig:
    """Everything a belief test needs, sharing one database file."""

    store: SQLiteEventStore
    types: EventTypeRegistry
    index_store: SQLiteBeliefIndexStore
    fold: BeliefFold
    projector: Projector
    index: BeliefIndex
    predicates: PredicateRegistry
    events: BeliefEvents = field(init=False)

    def __post_init__(self) -> None:
        self.events = BeliefEvents(self.types, self.predicates)

    @property
    def core(self) -> MemoryCore:
        store = self.fold.store
        assert isinstance(store, MemoryCore)
        return store

    def append(self, *events: PendingEvent) -> int:
        return self.store.append(events)

    def commit(self, *events: PendingEvent) -> int:
        """Append, then bring the projections current — the ordinary caller's rhythm."""
        last = self.append(*events)
        self.projector.catch_up()
        return last

    def asserted(self, predicate: str, *entity_ids: UUID, scope: str = "agent") -> PendingEvent:
        """A belief event for ``predicate`` with a value it accepts, for index-only tests."""
        relational = self.predicates.predicate(predicate).kind is PredicateKind.RELATIONAL
        return self.events.revise(scope, predicate, entity_ids, True if relational else "x")

    def rebuild(self) -> int:
        """Drop the belief store and the index, and fold both again from the log."""
        fresh = MemoryCore(InMemoryBackend())
        return self.projector.rebuild(
            FOLD_NAME, reset=lambda: self.fold.reset(fresh), reset_in_transaction=True
        )

    def believer(self, scope: str = "agent") -> Believer:
        return Believer(self, scope)


@dataclass
class Believer:
    """
    One scope's beliefs, written the only way they can be: as events, then folded.

    A test convenience with the shape the old writer had, so a test reads as what it
    asserts about belief rather than as plumbing. Each call appends one event, folds
    it, and returns the state it produced.
    """

    rig: Rig
    scope: str

    def for_scope(self, scope: str) -> Believer:
        return Believer(self.rig, scope)

    def revise(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,  # noqa: ANN401 -- belief values are opaque
        *,
        stance: Stance = Stance.certain,
        rests_on: BeliefState | None = None,
    ) -> BeliefState:
        edges = () if rests_on is None else (Edge(StateRef.of(rests_on)),)
        event = self.rig.events.revise(
            self.scope, predicate, entity_ids, value, stance=stance, edges=edges
        )
        return self._fold(event, predicate, entity_ids)

    def expand(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,  # noqa: ANN401 -- belief values are opaque
        *,
        stance: Stance = Stance.certain,
    ) -> BeliefState:
        event = self.rig.events.expand(self.scope, predicate, entity_ids, value, stance=stance)
        return self._fold(event, predicate, entity_ids)

    def contract(self, predicate: str, entity_ids: tuple[UUID, ...]) -> None:
        self.rig.commit(self.rig.events.contract(self.scope, predicate, entity_ids))

    def _fold(
        self, event: PendingEvent, predicate: str, entity_ids: tuple[UUID, ...]
    ) -> BeliefState:
        self.rig.commit(event)
        ref = StateRef(
            self.scope,
            self.rig.predicates.belief_id(predicate, *entity_ids),
            event.envelope.event_id,
        )
        state = self.rig.fold.state(ref)
        assert state is not None
        return state


def build_rig(
    path: Path,
    *,
    types: EventTypeRegistry | None = None,
    predicates: PredicateRegistry | None = None,
    core: MemoryCore | None = None,
    interpret: Mapping[tuple[str, str], Interpreter] | None = None,
) -> Rig:
    """Assemble a rig over ``path``; pass ``types`` to supply a custom vocabulary."""
    if types is None:
        types = EventTypeRegistry()
        register_builtin_events(types)
    predicates = predicates if predicates is not None else build_predicates()
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    index_store = SQLiteBeliefIndexStore(store.connection)
    fold = BeliefFold(
        core if core is not None else MemoryCore(InMemoryBackend()),
        predicates,
        index_store,
        types,
        interpret=interpret,
    )
    return Rig(
        store=store,
        types=types,
        index_store=index_store,
        fold=fold,
        projector=Projector(store, reader, [fold]),
        index=BeliefIndex(index_store),
        predicates=predicates,
    )


def beliefs_of(core: MemoryCore, scopes: tuple[str, ...]) -> list[tuple[object, ...]]:
    """
    Everything a set of scopes holds, history included, without the store's own handles.

    ``state_id`` is minted by doxastica and differs on every fold, so it is left out;
    every other field, every revision and every status is in. What a rebuild must
    reproduce exactly.
    """
    rows: list[tuple[object, ...]] = []
    for scope in scopes:
        tails = core.query_scope(
            scope, BeliefFilter(status=frozenset({Status.active, Status.retracted}))
        )
        for belief_id in sorted({t.belief_id for t in tails}):
            rows.extend(
                (s.scope_id, s.belief_id, s.source_event_id, repr(s.value), s.status, s.stance)
                for s in core.get_revision_chain(belief_id)
                if s.scope_id == scope
            )
    return rows


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    built = build_rig(tmp_path / "beliefs.db")
    try:
        yield built
    finally:
        built.store.close()


@pytest.fixture
def core(rig: Rig) -> MemoryCore:
    """The rig's real doxastica core, not a fake — the seam is worth exercising for real."""
    return rig.core


@pytest.fixture
def believer(rig: Rig) -> Believer:
    return rig.believer("agent")
