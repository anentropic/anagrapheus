"""
The storage ports.

``EventStore`` is the port the rest of the layer is built against: append is
atomic and
persist-first, reads filter on ``seq`` only — query semantics over event *content*
never cross this port; that is the projections' job. ``PinScratchStore`` is kept as a
separate protocol so ``EventStore`` stays exactly the catalogued port.

These protocols are the repository layer of this package, in ports-and-adapters form:
all SQL lives in one adapter module, callers code against the interface, and a
substitute store slots in with no call-site changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from contextlib import AbstractContextManager
    from uuid import UUID

    from anagrapheus.timeline._canonical import Json
    from anagrapheus.timeline.envelope import PendingEvent, StoredEvent

__all__ = ["EventStore", "LedgerStore", "PinScratchStore", "ScratchRow", "TransactionalStore"]


class EventStore(Protocol):
    """The timeline's storage port: one global stream, one writer."""

    def append(self, batch: Sequence[PendingEvent], *, expected_last_seq: int | None = None) -> int:
        """
        Append a batch atomically, persist-first; return the new last ``seq``.

        If ``expected_last_seq`` is given and stale, raise
        :class:`~anagrapheus.timeline.errors.ConcurrencyError` — the ``last_seq + 1``
        tripwire, not a coordination mechanism.

        Each event's hash is :func:`~anagrapheus.timeline.chain.event_hash` of the
        previous event's hash (the stream's last, for the batch's first) and its own
        envelope and payload, stored with it.
        """
        ...

    def read(self, since_seq: int = 0, limit: int | None = None) -> Iterator[StoredEvent]:
        """Read events with ``seq > since_seq``, in order, each with its stored hash."""
        ...

    def last_seq(self) -> int:
        """Return the highest assigned ``seq``, or 0 for an empty stream."""
        ...

    def read_position(self, projection: str) -> int:
        """Return the last ``seq`` a projection has folded, or 0 if never run."""
        ...

    def commit_position(self, projection: str, seq: int) -> None:
        """Record that a projection has folded through ``seq``."""
        ...


@runtime_checkable
class TransactionalStore(Protocol):
    """
    Optional capability: a store whose folds can share its transactions.

    When the projection's state lives in the same database as the stream, the
    projector applies each batch and commits the position inside one of these
    transactions, so they move together.
    """

    def transaction(self) -> AbstractContextManager[object]:
        """One explicit transaction; nested entry becomes a savepoint in the outer one."""
        ...

    @property
    def in_transaction(self) -> bool:
        """
        Whether a transaction is open on this store right now.

        The projector reads it to refuse work whose effects would outlive an outer
        transaction's rollback.
        """
        ...


class ScratchRow(NamedTuple):
    """One pinned result awaiting promotion or discard."""

    unit_id: UUID
    pin_key: str
    kind: str
    result: Json
    attempts: int


class PinScratchStore(Protocol):
    """
    Persistence for uncommitted pins.

    The scratch survives a crash but is never canon: nothing that reads the stream can
    observe it.
    """

    def scratch_get(self, unit_id: UUID, pin_key: str) -> ScratchRow | None:
        """Return the pinned row for a key within a unit of work, if any."""
        ...

    def scratch_put(self, row: ScratchRow) -> None:
        """Persist a fresh pin."""
        ...

    def scratch_bump(self, unit_id: UUID, pin_key: str) -> int:
        """Increment and return the attempt count for an existing pin."""
        ...

    def scratch_list(self, unit_id: UUID) -> list[ScratchRow]:
        """Return every pin in a unit of work, in insertion order."""
        ...

    def scratch_clear(self, unit_id: UUID) -> None:
        """Drop every pin in a unit of work."""
        ...

    def scratch_units(self) -> list[UUID]:
        """
        Return every unit of work that has pins in the scratch, oldest first.

        A unit is listed from its first pin until it is committed or abandoned. After a
        crash these are the units left open, which the application can resume by
        reopening a ``PinLedger`` with the unit's id, or close. The list covers every
        unit in the store, including any another live process is still working on.
        """
        ...


class LedgerStore(EventStore, PinScratchStore, TransactionalStore, Protocol):
    """
    Everything a :class:`~anagrapheus.timeline.pins.PinLedger` needs from storage.

    A composite of the three capabilities — events, scratch, transactions — so any
    conforming adapter slots in under strict typing, not only the SQLite one.
    """
