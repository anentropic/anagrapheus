"""
The pin ledger: nondeterminism recorded under a key.

Pinning records the result of a nondeterministic effect — a model call, an RNG draw, a
tool invocation, an external fetch — so the effect happens once and everything
afterwards reuses the record. Within a unit of work that is reuse-under-retry; across
runs it is replay.

The whole design is in the key: hash only what consumes the entropy, never the
interpretation. ``resolve`` takes the effect and its interpretation together and
returns only the interpreted outcome — the caller never sees the raw draw, so a
threshold cannot be fitted to a number after it is known. That closes the fishing
failure structurally rather than by discipline.

What counts as "the same call" — the canonicalization policy that builds the key
material — is the application's to own. This module owns the mechanism.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from pydantic import JsonValue

from anagrapheus.timeline._canonical import Json, sha256_hex
from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey
from anagrapheus.timeline.store import ScratchRow

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.registry import EventTypeRegistry
    from anagrapheus.timeline.sqlite import SQLiteEventStore

__all__ = [
    "DiscardedPin",
    "PinLedger",
    "PinPromoted",
    "PinResolver",
    "PinsAbandoned",
    "ReplayDiverged",
    "pin_key",
    "register_builtin_events",
]

type PinKey = str


def pin_key(kind: str, material: Mapping[str, Json], *, scheme: int = 1) -> PinKey:
    """
    Build a content-addressed pin key: ``p<scheme>:<kind>:<sha256>``.

    ``material`` is the canonicalization policy made concrete: include exactly what
    consumes the entropy — for a model call, the semantic identity of the call — and
    nothing about how the result will be read. The scheme prefix makes a policy
    change loud: bump it and every old key visibly misses instead of silently
    matching.
    """
    return f"p{scheme}:{kind}:{sha256_hex(dict(material))}"


class PinResolver(Protocol):
    """
    The seam agent code draws entropy through.

    Both the live ledger and replay implement it, so a unit of work is written once
    and runs identically live, under retry, and under replay.
    """

    def resolve[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], R],
        interpret: Callable[[R], O],
    ) -> O:
        """Run (or reuse) the effect under ``key``; return only the interpretation."""
        ...


class PinPromoted(DomainEvent):
    """A committed pin, promoted into the log as an ordinary event."""

    unit_id: UUID
    key: str
    kind: str
    result: JsonValue
    attempts: int


class DiscardedPin(DomainEvent):
    """One abandoned pin's audit line inside :class:`PinsAbandoned`."""

    key: str
    kind: str
    attempts: int


class PinsAbandoned(DomainEvent):
    """The single summary event an abandoned unit of work leaves behind."""

    unit_id: UUID
    reason: str
    discarded: list[DiscardedPin]


class ReplayDiverged(DomainEvent):
    """A generation-replay met a key with no pinned result: the run is announced new."""

    unit_id: UUID
    missed_key: str
    kind: str


def register_builtin_events(registry: EventTypeRegistry) -> None:
    """Register this library's own events under the reserved namespace."""
    registry._register(EventKey(RESERVED_NAMESPACE, "PinPromoted", 1), PinPromoted)  # noqa: SLF001
    registry._register(EventKey(RESERVED_NAMESPACE, "PinsAbandoned", 1), PinsAbandoned)  # noqa: SLF001
    registry._register(EventKey(RESERVED_NAMESPACE, "ReplayDiverged", 1), ReplayDiverged)  # noqa: SLF001


class PinLedger:
    """
    The live resolver for one unit of work.

    Lifecycle: draw -> pin in the scratch (persistent, never canon); retry with the
    same key -> reuse; ``commit`` -> pins promote into the log as ordinary events;
    ``abandon`` -> the scratch drops, leaving one summary event recording attempts and
    discards. The log stays pure either way.
    """

    def __init__(
        self,
        store: SQLiteEventStore,
        registry: EventTypeRegistry,
        *,
        unit_id: UUID | None = None,
        promote_as: Callable[[ScratchRow], PendingEvent] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self.unit_id = unit_id if unit_id is not None else uuid.uuid7()
        self._promote_as = promote_as

    def resolve[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], R],
        interpret: Callable[[R], O],
    ) -> O:
        """
        Reuse the pinned raw result for ``key``, or run the effect exactly once.

        The raw result never escapes: it is drawn (or fetched from the scratch) and
        interpreted inside this call. A retry with the same key reuses; a materially
        different call is a different key and draws fresh.
        """
        existing = self._store.scratch_get(self.unit_id, key)
        if existing is not None:
            self._store.scratch_bump(self.unit_id, key)
            # The stored value round-tripped through JSON; the key guarantees it is
            # the same draw this effect would have produced.
            raw = cast("R", existing.result)
        else:
            raw = effect()
            self._store.scratch_put(
                ScratchRow(unit_id=self.unit_id, pin_key=key, kind=kind, result=raw, attempts=1)
            )
        return interpret(raw)

    def commit(self, *, correlation_id: UUID | None = None) -> int:
        """
        Promote every scratch pin into the log and clear the scratch, atomically.

        Pins become ordinary events — :class:`PinPromoted` by default, or whatever the
        ``promote_as`` factory returns, so applications can promote into their own
        vocabulary. Returns the new last ``seq`` (unchanged if nothing was pinned).
        """
        rows = self._store.scratch_list(self.unit_id)
        with self._store.transaction():
            batch = [self._promotion(row, correlation_id) for row in rows]
            last_seq = self._store.append(batch) if batch else self._store.last_seq()
            self._store.scratch_clear(self.unit_id)
        return last_seq

    def abandon(self, *, reason: str = "") -> int:
        """
        Drop the scratch, appending one summary event recording attempts and discards.

        The audit trail is a single :class:`PinsAbandoned`, not a permanent pile of
        provisional entries. Returns the new last ``seq``.
        """
        rows = self._store.scratch_list(self.unit_id)
        summary = PinsAbandoned(
            unit_id=self.unit_id,
            reason=reason,
            discarded=[
                DiscardedPin(key=row.pin_key, kind=row.kind, attempts=row.attempts) for row in rows
            ],
        )
        with self._store.transaction():
            last_seq = self._store.append([self._registry.pending(summary)])
            self._store.scratch_clear(self.unit_id)
        return last_seq

    def _promotion(self, row: ScratchRow, correlation_id: UUID | None) -> PendingEvent:
        if self._promote_as is not None:
            return self._promote_as(row)
        promoted = PinPromoted(
            unit_id=row.unit_id,
            key=row.pin_key,
            kind=row.kind,
            # Json -> JsonValue: pydantic validates the JSON-shaped value on construction.
            result=cast("JsonValue", row.result),
            attempts=row.attempts,
        )
        return self._registry.pending(promoted, correlation_id=correlation_id)
