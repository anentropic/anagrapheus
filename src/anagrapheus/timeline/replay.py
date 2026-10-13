"""
Generation-replay: rerun a unit of work from its promoted pins.

Same log, same code, same prompt assembly -> same run. Change the prompt assembly and
the keys miss — and a miss is detectable: strict mode raises at the first miss, record
mode marks the run divergent and continues by drawing fresh. Either way it is a new
run continuing the same history, announced — never a different run wearing the word
"replay".

Record mode draws through a :class:`~anagrapheus.timeline.pins.PinLedger` passed as
``into=``, so the new run is a unit of work like any other: its fresh draws and the pins
it replayed both land in that ledger's scratch, and ``ledger.commit()`` promotes them
with a :class:`~anagrapheus.timeline.pins.ReplayDiverged` in one transaction. The new
run's log then replays the new run strictly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, Self, cast

from anyio import Event
from pydantic import BaseModel

from anagrapheus.timeline._canonical import Json, concrete_json
from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.pins import PinKey, PinPromoted, ReplayDiverged
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterable
    from contextlib import AbstractAsyncContextManager
    from uuid import UUID

    from anagrapheus.timeline.pins import PinLedger
    from anagrapheus.timeline.registry import EventTypeRegistry
    from anagrapheus.timeline.store import EventStore
    from anagrapheus.timeline.upcast import UpcastRegistry


__all__ = ["Divergence", "PinMiss", "ReplayPins"]

_log = logging.getLogger(__name__)

type ExtractFn = Callable[[StoredEvent, DomainEvent], tuple[PinKey, Json] | None]


class Divergence(BaseModel, frozen=True):
    """Where a replay stopped being a replay: the first key with no pinned result."""

    missed_key: str
    kind: str
    after_hits: int


class PinMiss(TimelineError):
    """Strict replay met a key with no pinned result."""

    def __init__(self, divergence: Divergence) -> None:
        super().__init__(
            f"replay diverged at key {divergence.missed_key!r} "
            f"({divergence.kind}) after {divergence.after_hits} hit(s)"
        )
        self.divergence = divergence


class ReplayPins:
    """
    A resolver that answers from promoted pins instead of running effects.

    Implements the same ``PinResolver`` interface as the live ledger, so a unit of
    work is written once and replays without modification. ``on_miss`` picks the posture:
    ``"strict"`` raises :class:`PinMiss`; ``"record"`` marks the divergence at the
    first miss and falls through to the effect — the run continues, announced as new.

    Record mode needs ``into``, the :class:`~anagrapheus.timeline.pins.PinLedger` the
    new run is recorded in. A miss draws through it, so the fresh draw lands in its
    scratch; a hit is adopted into the same scratch, so the ledger holds the whole new
    run and not only the part that differed. ``into.commit()`` then promotes those pins
    and a :class:`~anagrapheus.timeline.pins.ReplayDiverged` for the first miss, in one
    transaction. Without ``into`` a record-mode run's draws would reach no log, so it
    is refused.
    """

    def __init__(
        self,
        pins: dict[str, Json],
        *,
        on_miss: Literal["strict", "record"] = "strict",
        announce: Callable[[Divergence], None] | None = None,
        into: PinLedger | None = None,
    ) -> None:
        if on_miss not in ("strict", "record"):
            # Literal annotations are not enforced at runtime; a typo must not
            # silently weaken strict replay into live draws.
            raise ValueError(f"on_miss must be 'strict' or 'record', got {on_miss!r}")
        if on_miss == "record" and into is None:
            raise ValueError(
                "on_miss='record' needs into=PinLedger(...): a record-mode run draws "
                "fresh, and without a ledger those draws would never reach the log"
            )
        if on_miss == "strict" and into is not None:
            raise ValueError("into= records a new run; it applies only to on_miss='record'")
        self._pins = pins
        self._on_miss: Literal["strict", "record"] = on_miss
        self._announce = announce
        self._into = into
        self._hits = 0
        # One lock per key, so concurrent draws for one key coalesce; see `_lock_for`.
        # Keys with a draw in flight right now, plain or streamed.
        self._inflight: dict[PinKey, Event] = {}
        self._divergence: Divergence | None = None

    @staticmethod
    def announce_to(
        store: EventStore, registry: EventTypeRegistry, *, unit_id: UUID
    ) -> Callable[[Divergence], None]:
        """
        An ``announce`` callback that appends a :class:`ReplayDiverged` event at once.

        Not needed for record mode, whose ``into`` ledger already promotes the
        divergence with the pins at ``commit``; wiring both writes it twice. Use it when
        the divergence must reach the log even if the new run is never committed.
        """

        def announce(divergence: Divergence) -> None:
            event = ReplayDiverged(
                unit_id=unit_id, missed_key=divergence.missed_key, kind=divergence.kind
            )
            store.append([registry.pending(event)])

        return announce

    @classmethod
    def from_events(
        cls,
        events: Iterable[StoredEvent],
        registry: EventTypeRegistry,
        *,
        unit_id: UUID | None = None,
        extract: ExtractFn | None = None,
        upcasts: UpcastRegistry | None = None,
        on_miss: Literal["strict", "record"] = "strict",
        announce: Callable[[Divergence], None] | None = None,
        into: PinLedger | None = None,
    ) -> Self:
        """
        Build a replay source from a stream (or fixture) of stored events.

        The default understands :class:`PinPromoted`; applications that promoted pins
        into their own vocabulary supply ``extract`` — the mirror of ``promote_as``,
        which then owns all filtering (``unit_id`` cannot be combined with it).
        ``unit_id`` narrows to one unit of work; omitted, every pin in the stream
        loads (later pins win on key collision). Pass ``upcasts`` when the events did
        not already go through an :class:`EventReader` — raw fixtures, for example —
        so pin event payloads get the same upcast-on-read as everything else.
        ``on_miss``, ``announce`` and ``into`` are as for the class.
        """
        if extract is not None and unit_id is not None:
            raise ValueError("unit_id cannot narrow custom-promoted events; filter inside extract")
        pins: dict[str, Json] = {}
        for stored in events:
            env = stored.envelope
            key = EventKey(env.namespace, env.type, env.schema_version)
            if extract is None and (env.namespace, env.type) != (
                RESERVED_NAMESPACE,
                "PinPromoted",
            ):
                continue
            payload = stored.payload
            if upcasts is not None:
                target = registry.latest_version(env.namespace, env.type)
                version, payload = upcasts.apply(key, payload, target_version=target)
                key = EventKey(env.namespace, env.type, version)
            decoded = registry.decode(key, payload)
            if extract is not None:
                pair = extract(stored, decoded)
                if pair is not None:
                    pins[pair[0]] = pair[1]
                continue
            assert isinstance(decoded, PinPromoted)
            if unit_id is not None and decoded.unit_id != unit_id:
                continue
            pins[decoded.key] = cast("Json", decoded.result)
        return cls(pins, on_miss=on_miss, announce=announce, into=into)

    @property
    def divergence(self) -> Divergence | None:
        """The first miss, if this replay has stopped being a replay."""
        return self._divergence

    @property
    def ledger(self) -> PinLedger | None:
        """The ledger a record-mode run draws into (``into``), or ``None`` for strict."""
        return self._into

    def resolve[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], R],
        interpret: Callable[[R], O],
    ) -> O:
        """
        Answer from the pinned result for ``key``; on a miss, follow ``on_miss``.

        Strict mode raises on every miss, not only the first — catching one
        :class:`PinMiss` must not turn later misses into silent live draws. Record
        mode marks the first miss, then draws through the ``into`` ledger, so replayed
        and fresh keys alike keep reuse-under-retry semantics and reach its scratch.
        """
        if key in self._pins:
            return interpret(cast("R", self._hit(key, kind)))
        ledger = self._miss(key, kind)
        return interpret(
            self._remember(key, ledger.resolve(key, kind=kind, effect=effect, interpret=_raw))
        )

    async def resolve_async[R: Json, O](
        self,
        key: PinKey,
        *,
        kind: str,
        effect: Callable[[], Awaitable[R]],
        interpret: Callable[[R], O],
    ) -> O:
        """
        The awaited twin of :meth:`resolve`, following the same ``on_miss`` policy.

        Under strict replay the effect is never awaited at all: the miss raises before
        the call is made, which is what makes "no model was reached" an assertion a test
        can hold rather than a hope.
        """
        # Same shared per-key gate as the live ledger, for the same reason: record mode
        # pins a fresh draw, so it has the same gap between the check and the write --
        # and it must coordinate with `resolve_stream`, since a streamed call and a
        # plain one share a key.
        await self._await_inflight(key)
        if key in self._pins:
            return interpret(cast("R", self._hit(key, kind)))
        ledger = self._miss(key, kind)
        gate = self._begin(key)
        try:
            raw = await ledger.resolve_async(key, kind=kind, effect=effect, interpret=_raw)
        finally:
            self._end(key, gate)
        return interpret(self._remember(key, raw))

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
        Replay a streamed effect from the log, following the same ``on_miss`` policy.

        Under strict replay the stream is **never opened**: the miss raises before
        ``effect`` is entered, so "no model was reached" stays an assertion a test can
        hold rather than a hope — the same property :meth:`resolve_async` has, carried
        through a path where the connection would otherwise be made before anyone knew
        whether it was needed.

        What comes back is a stream rebuilt from the pinned response, so its chunk
        boundaries and timing are this library's rather than the provider's. It
        replays *a* stream, which is not the same claim as having streamed.
        """
        await self._await_inflight(key)
        if key in self._pins:
            yield interpret(cast("R", self._hit(key, kind)))
            return

        # After the wait, so a caller that ends up reusing another's pin never counts
        # as a divergence.
        ledger = self._miss(key, kind)
        gate = self._begin(key)
        try:
            # The ledger records even an abandoned stream, as it does live: record mode
            # builds a corpus, and a partial response is part of what happened. The
            # wrappers keep a copy here too, so later calls on this key are hits.
            async with ledger.resolve_stream(
                key,
                kind=kind,
                effect=effect,
                capture=lambda live: self._remember(key, capture(live)),
                interpret=lambda raw: interpret(self._remember(key, raw)),
            ) as stream:
                yield stream
        finally:
            self._end(key, gate)

    async def _await_inflight(self, key: PinKey) -> None:
        """
        Block while another caller is drawing this key, plain or streamed.

        One mechanism for both async shapes, as on the live ledger — the two transports
        share a key by design, so two independent guards would leave exactly the gap
        each closed on its own path. Ownerless, because `resolve_stream` holds its claim
        across a ``yield`` that another task may finalize.
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

    def _hit(self, key: PinKey, kind: str) -> Json:
        """Answer from the pins; in record mode, adopt the answer into the new run."""
        self._hits += 1
        pinned = self._pins[key]
        if self._into is not None:
            pinned = self._into.adopt(key, kind=kind, result=pinned)
        return pinned

    def _miss(self, key: PinKey, kind: str) -> PinLedger:
        """
        Apply the ``on_miss`` policy: raise, or mark the first and carry on.

        Returns the ledger to draw through, which record mode always has.
        """
        miss = Divergence(missed_key=key, kind=kind, after_hits=self._hits)
        _log.warning("replay missed %s after %d hit(s) (%s)", key, self._hits, self._on_miss)
        if self._on_miss == "strict" or self._into is None:
            if self._divergence is None:
                self._divergence = miss
            raise PinMiss(miss)
        if self._divergence is None:
            self._divergence = miss
            self._into.mark_diverged(missed_key=key, kind=kind)
            if self._announce is not None:
                self._announce(miss)
        return self._into

    def _remember[R: Json](self, key: PinKey, raw: R) -> R:
        """Keep a drawn result here too, so later calls on this key are hits."""
        raw = cast("R", concrete_json(raw))
        self._pins[key] = cast("Json", raw)
        return raw


def _raw[R](raw: R) -> R:
    """The identity ``interpret``: the ledger hands back the concretized raw result."""
    return raw
