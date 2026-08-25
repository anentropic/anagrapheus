"""Wiring shared by the identity suite: one file, one connection, one projector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from anagrapheus.identity import (
    IdentityFold,
    IdentityRegistry,
    SQLiteIdentityStore,
    register_builtin_events,
)
from anagrapheus.identity.fold import FOLD_NAME
from anagrapheus.timeline import (
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


@dataclass
class Rig:
    """Everything an identity test needs, sharing one database file."""

    store: SQLiteEventStore
    types: EventTypeRegistry
    identity_store: SQLiteIdentityStore
    fold: IdentityFold
    projector: Projector
    registry: IdentityRegistry

    def append(self, *events: PendingEvent) -> int:
        return self.store.append(events)

    def commit(self, *events: PendingEvent) -> int:
        """Append, then bring the registry current — the ordinary caller's rhythm."""
        last = self.append(*events)
        self.projector.catch_up()
        return last

    def rebuild(self) -> int:
        return self.projector.rebuild(
            FOLD_NAME,
            reset=self.identity_store.reset,
            reset_in_transaction=True,
        )


def build_rig(path: Path, *, types: EventTypeRegistry | None = None) -> Rig:
    """Assemble a rig over ``path``; pass ``types`` to supply a custom vocabulary."""
    if types is None:
        types = EventTypeRegistry()
        register_builtin_events(types)
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    identity_store = SQLiteIdentityStore(store.connection)
    fold = IdentityFold(identity_store, types)
    return Rig(
        store=store,
        types=types,
        identity_store=identity_store,
        fold=fold,
        projector=Projector(store, reader, [fold]),
        registry=IdentityRegistry(identity_store),
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    built = build_rig(tmp_path / "identity.db")
    try:
        yield built
    finally:
        built.store.close()


def forms(rows: Sequence[object]) -> list[str]:
    """Surface forms out of alias rows, for terse assertions."""
    return sorted(getattr(row, "surface_form") for row in rows)  # noqa: B009
