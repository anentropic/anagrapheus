"""
Wiring shared by the chronicle suite.

The summarizer here is **counted and deliberately unstable** — it stamps each
consolidation with a generation number, so two runs over the same range produce
different prose. That is the point: a summarizer that returned the same string twice
would make anchor-replay pass for the wrong reason, since the prose would match by
accident and the test would prove nothing about which half is invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from anagrapheus import register_builtin_events
from anagrapheus.chronicle import (
    Anchors,
    Brief,
    ChronicleFold,
    Consolidation,
    EventTypes,
    Prose,
    SQLiteChronicleStore,
)
from anagrapheus.chronicle.fold import FOLD_NAME
from anagrapheus.timeline import (
    DomainEvent,
    EventKey,
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from anagrapheus.timeline import PendingEvent
    from anagrapheus.timeline.envelope import StoredEvent

NAMESPACE = "saga"


class StepTaken(DomainEvent, frozen=True):
    """An ordinary domain event, the stuff a unit is made of."""

    what: str


class UnitClosed(DomainEvent, frozen=True):
    """The application's marker: this is what ends a unit of work."""

    label: str


@dataclass
class CountingSummarizer:
    """Records every call, and never returns the same prose twice."""

    calls: list[tuple[int, int]] = field(default_factory=list)
    generation: int = 0

    def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        self.generation += 1
        self.calls.append((events[0].seq, events[-1].seq))
        summary = ", ".join(str(e.payload.get("what", e.envelope.type)) for e in events)
        # The generation prefix is what makes two consolidations of one range differ.
        return Consolidation(prose=f"[g{self.generation}] {summary}")


@dataclass
class Rig:
    """Everything a chronicle test needs, sharing one database file."""

    store: SQLiteEventStore
    types: EventTypeRegistry
    chronicle: SQLiteChronicleStore
    fold: ChronicleFold
    projector: Projector
    summarizer: CountingSummarizer
    brief: Brief

    @property
    def anchors(self) -> Anchors:
        return Anchors(self.chronicle)

    @property
    def prose(self) -> Prose:
        return Prose(self.chronicle)

    def append(self, *events: PendingEvent) -> int:
        return self.store.append(events)

    def commit(self, *events: PendingEvent) -> int:
        """Append, then bring the chronicle current."""
        last = self.append(*events)
        self.projector.catch_up()
        return last

    def step(self, what: str) -> PendingEvent:
        return self.types.pending(StepTaken(what=what))

    def close(self, label: str) -> PendingEvent:
        return self.types.pending(UnitClosed(label=label))

    def rebuild(self, *, discard_generated: bool = True) -> int:
        return self.projector.rebuild(
            FOLD_NAME,
            reset=self.chronicle.reset,
            reset_in_transaction=True,
            discard_generated=discard_generated,
        )


def build_rig(path: Path) -> Rig:
    types = EventTypeRegistry()
    register_builtin_events(types)
    types.register(EventKey(NAMESPACE, "StepTaken", 1), StepTaken)
    types.register(EventKey(NAMESPACE, "UnitClosed", 1), UnitClosed)
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    chronicle = SQLiteChronicleStore(store.connection)
    summarizer = CountingSummarizer()
    fold = ChronicleFold(
        chronicle,
        reader,
        boundary=EventTypes((NAMESPACE, "UnitClosed")),
        summarize=summarizer,
    )
    return Rig(
        store=store,
        types=types,
        chronicle=chronicle,
        fold=fold,
        projector=Projector(store, reader, [fold]),
        summarizer=summarizer,
        brief=Brief(chronicle, reader),
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    built = build_rig(tmp_path / "chronicle.db")
    try:
        yield built
    finally:
        built.store.close()
