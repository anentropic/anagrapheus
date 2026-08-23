"""
The storage ports.

``EventStore`` is the seam the whole layer stands on: append is atomic and
persist-first, reads filter on ``seq`` only — query semantics over event *content*
never cross this port; that is the projections' job. ``PinScratchStore`` is kept as a
separate protocol so ``EventStore`` stays exactly the catalogued port.

These protocols are the repository layer of this package, in ports-and-adapters form:
all SQL lives in one adapter module, callers code against the interface, and a
substitute store slots in with no call-site changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from uuid import UUID

    from pydantic import JsonValue

    from anagrapheus.timeline.envelope import PendingEvent, StoredEvent

__all__ = ["EventStore", "PinScratchStore", "ScratchRow"]


class EventStore(Protocol):
    """The timeline's storage port: one global stream, one writer."""

    def append(self, batch: Sequence[PendingEvent], *, expected_last_seq: int | None = None) -> int:
        """
        Append a batch atomically, persist-first; return the new last ``seq``.

        If ``expected_last_seq`` is given and stale, raise
        :class:`~anagrapheus.timeline.errors.ConcurrencyError` — the ``last_seq + 1``
        tripwire, not a coordination mechanism.
        """
        ...

    def read(self, since_seq: int = 0, limit: int | None = None) -> Iterator[StoredEvent]:
        """Read events with ``seq > since_seq``, in order. No content filtering."""
        ...

    def last_seq(self) -> int:
        """Return the highest assigned ``seq``, or 0 for an empty stream."""
        ...

    def read_watermark(self, projection: str) -> int:
        """Return the last ``seq`` a projection has folded, or 0 if never run."""
        ...

    def commit_watermark(self, projection: str, seq: int) -> None:
        """Record that a projection has folded through ``seq``."""
        ...


class ScratchRow(NamedTuple):
    """One pinned result awaiting promotion or discard."""

    unit_id: UUID
    pin_key: str
    kind: str
    result: JsonValue
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
