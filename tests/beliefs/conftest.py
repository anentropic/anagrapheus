"""Wiring shared by the belief suite: one file, one connection, one projector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from doxastica import InMemoryBackend, MemoryCore

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    BeliefIndex,
    BeliefIndexFold,
    BeliefWriter,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    SQLiteBeliefIndexStore,
)
from anagrapheus.beliefs.fold import FOLD_NAME
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from anagrapheus.timeline import PendingEvent

# A test application's own predicates, declared exactly as a consumer would: in code.
PREDICATES = (
    Predicate("location", 1, PredicateKind.FUNCTIONAL),
    Predicate("employer", 1, PredicateKind.FUNCTIONAL),
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
    fold: BeliefIndexFold
    projector: Projector
    index: BeliefIndex
    predicates: PredicateRegistry

    def append(self, *events: PendingEvent) -> int:
        return self.store.append(events)

    def commit(self, *events: PendingEvent) -> int:
        """Append, then bring the index current — the ordinary caller's rhythm."""
        last = self.append(*events)
        self.projector.catch_up()
        return last

    def rebuild(self) -> int:
        return self.projector.rebuild(
            FOLD_NAME,
            reset=self.index_store.reset,
            reset_in_transaction=True,
        )


def build_rig(path: Path, *, types: EventTypeRegistry | None = None) -> Rig:
    """Assemble a rig over ``path``; pass ``types`` to supply a custom vocabulary."""
    if types is None:
        types = EventTypeRegistry()
        register_builtin_events(types)
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    index_store = SQLiteBeliefIndexStore(store.connection)
    fold = BeliefIndexFold(index_store, types)
    return Rig(
        store=store,
        types=types,
        index_store=index_store,
        fold=fold,
        projector=Projector(store, reader, [fold]),
        index=BeliefIndex(index_store),
        predicates=build_predicates(),
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    built = build_rig(tmp_path / "beliefs.db")
    try:
        yield built
    finally:
        built.store.close()


@pytest.fixture
def core() -> MemoryCore:
    """A real doxastica core, not a fake — the seam is worth exercising for real."""
    return MemoryCore(InMemoryBackend())


@pytest.fixture
def writer(core: MemoryCore) -> BeliefWriter:
    types = EventTypeRegistry()
    register_builtin_events(types)
    return BeliefWriter(core, build_predicates(), types, scope_id="agent")
