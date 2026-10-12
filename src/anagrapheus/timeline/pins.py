"""
The pin ledger: nondeterminism recorded under a key.

Pinning records the result of a nondeterministic effect — a model call, an RNG draw, a
tool invocation, an external fetch — so the effect happens once and everything
afterwards reuses the record. Within a unit of work that is reuse-under-retry; across
runs it is replay.

The whole design is in the key: hash only what consumes the entropy, never the
interpretation. ``resolve`` takes the effect and its interpretation together and
returns only the interpreted outcome. What that guarantees: entropy is consumed once
per key, and re-reading a result never requires re-drawing it — an interpretation
change reuses the pinned raw. What it does not guarantee: ``interpret`` is
caller-supplied code that receives the raw value, so a caller determined to leak it
can. The discipline is made auditable at the call site, not impossible.

What counts as "the same call" — the canonicalization policy that builds the key
material — is the application's to own. This module owns the mechanism.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from anyio import Event
from pydantic import JsonValue

from anagrapheus.timeline._canonical import Json, concrete_json, sha256_hex
from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey
from anagrapheus.timeline.store import ScratchRow

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
    from contextlib import AbstractAsyncContextManager

    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.registry import EventTypeRegistry
    from anagrapheus.timeline.store import LedgerStore

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

_log = logging.getLogger(__name__)

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
    The interface through which agent code performs nondeterministic effects.

    Both the live ledger and replay implement it, so a unit of work is written once
    and runs identically live, under retry, and under replay.

    Three shapes, one policy. :meth:`resolve` is for a synchronous effect;
    :meth:`resolve_async` is for an awaited one, because the effects most worth pinning
    — a model call above all — are async in every library that makes them;
    :meth:`resolve_stream` is for an effect whose result exists only once the *caller*
    has consumed it. They differ only in how the draw is performed: the key, the reuse,
    the concretization and the interpretation are identical, and the implementations
    share them rather than keeping two copies of a rule that could drift apart.
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

    async def resolve_async[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], Awaitable[R]],
        interpret: Callable[[R], O],
    ) -> O:
        """Await (or reuse) the effect under ``key``; return only the interpretation."""
        ...

    def resolve_stream[R: Json, S](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], AbstractAsyncContextManager[S]],
        capture: Callable[[S], R],
        interpret: Callable[[R], S],
    ) -> AbstractAsyncContextManager[S]:
        """
        Open (or replay) a streamed effect under ``key``.

        Three callables rather than two, because a stream's result does not exist at
        the moment of the draw. ``effect`` opens the live stream, ``capture`` reduces
        it to the pinned value **after the caller has consumed it**, and ``interpret``
        rebuilds a stream from that value on reuse. The extra one is the price of the
        shape: :meth:`resolve_async` can pin what its effect returns, and this cannot.
        """
        ...


class PinPromoted(DomainEvent, frozen=True):
    """A committed pin, promoted into the log as an ordinary event."""

    unit_id: UUID
    key: str
    kind: str
    result: JsonValue
    attempts: int


class DiscardedPin(DomainEvent, frozen=True):
    """One abandoned pin's audit line inside :class:`PinsAbandoned`."""

    key: str
    kind: str
    attempts: int


class PinsAbandoned(DomainEvent, frozen=True):
    """The single summary event an abandoned unit of work leaves behind."""

    unit_id: UUID
    reason: str
    discarded: list[DiscardedPin]


class ReplayDiverged(DomainEvent, frozen=True):
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
        store: LedgerStore,
        registry: EventTypeRegistry,
        *,
        unit_id: UUID | None = None,
        promote_as: Callable[[ScratchRow], PendingEvent] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self.unit_id = unit_id if unit_id is not None else uuid.uuid7()
        self._promote_as = promote_as
        # Keys with a draw in flight right now, plain or streamed. One gate per key,
        # removed as soon as the draw ends, so this never grows with the corpus.
        self._inflight: dict[PinKey, Event] = {}

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

        The raw result is drawn (or fetched from the scratch) and handed only to the
        ``interpret`` passed in with the draw. A retry with the same key reuses; a
        materially different call is a different key and draws fresh.
        """
        found, pinned = self._reuse(key)
        raw = cast("R", pinned) if found else self._record(key, kind, effect())
        return interpret(raw)

    async def resolve_async[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], Awaitable[R]],
        interpret: Callable[[R], O],
    ) -> O:
        """
        The awaited twin of :meth:`resolve`, for an effect that is a coroutine.

        Identical in every respect that matters — same reuse, same concretization, same
        scratch row — because it is the same policy with the draw awaited. The effect is
        awaited **only** on a miss, so a reused pin costs no call at all rather than one
        started and thrown away.
        """
        # The await below is a suspension point in exactly the gap `resolve` does not
        # have. Without coordination two concurrent callers both miss, both draw, and
        # the second write hits the primary key -- breaking the one promise this layer
        # makes. The gate is shared with `resolve_stream` and keyed, so a streamed and
        # a plain call on one key coalesce while different keys still run concurrently.
        await self._await_inflight(key)
        found, pinned = self._reuse(key)
        if found:
            return interpret(cast("R", pinned))
        gate = self._begin(key)
        try:
            raw = self._record(key, kind, await effect())
        finally:
            self._end(key, gate)
        return interpret(raw)

    @asynccontextmanager
    async def resolve_stream[R: Json, S](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], AbstractAsyncContextManager[S]],
        capture: Callable[[S], R],
        interpret: Callable[[R], S],
    ) -> AsyncGenerator[S]:
        """
        Open the stream once under ``key``; replay it from the pin thereafter.

        The same policy as :meth:`resolve_async` with the draw spread over time. What
        differs is *when* the pin is written: not at the draw, because a stream has no
        result then, but once the caller is done with it.

        **An abandoned stream still pins.** If the consumer breaks out early or the
        stream errors mid-way, what arrived is recorded with the state the provider
        stamped on it — ``incomplete`` or ``interrupted`` rather than ``complete``. A
        partial response is an outcome the model produced, and replaying it as the same
        partial is more faithful than either discarding it or pretending it finished.
        """
        await self._await_inflight(key)
        found, pinned = self._reuse(key)
        if found:
            # Never opens the effect: a reused pin costs no connection at all.
            yield interpret(cast("R", pinned))
            return

        gate = self._begin(key)
        try:
            async with effect() as live:
                try:
                    yield live
                finally:
                    # In a `finally` so an abandoned or failed stream still records what
                    # arrived. `capture` reads the accumulation, which is well-defined
                    # at any point, so this is safe on the exception path too.
                    self._record(key, kind, capture(live))
        finally:
            self._end(key, gate)

    async def _await_inflight(self, key: PinKey) -> None:
        """
        Block while another caller is drawing this key, however it is drawing it.

        **One mechanism for both async shapes**, which is what makes a streamed call and
        a plain one on the same key coalesce rather than race. They *do* share a key by
        design — see the provider layer's note on transport not being part of the call's
        identity — so two independent guards would leave exactly the gap they each
        closed on their own path.

        Not an ``anyio.Lock``, because :meth:`resolve_stream` holds its claim across a
        ``yield`` and a lock is task-affine: it refuses a release by a task that did not
        acquire it, and a stream may be finalized by a different task than opened it. An
        ``Event`` has no owner. The loop re-checks after waking, since the waiter it was
        waiting on may have been replaced by another claimant.
        """
        while (gate := self._inflight.get(key)) is not None:
            await gate.wait()

    def _begin(self, key: PinKey) -> Event:
        """Claim this key for the current draw. Safe without a lock: no await here."""
        gate = self._inflight[key] = Event()
        return gate

    def _end(self, key: PinKey, gate: Event) -> None:
        """Release the claim and wake everyone waiting on it, drawn or failed."""
        del self._inflight[key]
        gate.set()

    def _reuse(self, key: PinKey) -> tuple[bool, Json]:
        """The pinned result for ``key`` and whether there was one, bumping attempts."""
        existing = self._store.scratch_get(self.unit_id, key)
        if existing is None:
            # `None` is a legitimate pinned result, so the flag carries the answer and
            # the value never has to stand in for "nothing".
            return False, None
        self._store.scratch_bump(self.unit_id, key)
        _log.debug("pin reused: %s", key)
        # The stored value round-tripped through JSON; the key guarantees it is
        # the same draw this effect would have produced.
        return True, existing.result

    def _record[R: Json](self, key: PinKey, kind: str, drawn: R) -> R:
        """Concretize a fresh draw and pin it in the scratch."""
        # Concretize BEFORE both persistence and the first interpretation, so
        # interpret sees the identical shape on first resolve, retry, and
        # replay — a tuple is a list everywhere, and an unserializable result
        # fails here rather than after it has been half-consumed.
        raw = cast("R", concrete_json(drawn))
        self._store.scratch_put(
            ScratchRow(unit_id=self.unit_id, pin_key=key, kind=kind, result=raw, attempts=1)
        )
        _log.debug("pin drawn: %s", key)
        return raw

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
        _log.info("committed %d pin(s) from unit %s into the log", len(rows), self.unit_id)
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
        _log.warning("abandoned unit %s, discarding %d pin(s): %s", self.unit_id, len(rows), reason)
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
