"""
Generation-replay: rerun a unit of work from its promoted pins.

Same log, same code, same prompt assembly -> same run. Change the prompt assembly and
the keys miss — and a miss is detectable: strict mode raises at the first miss, record
mode marks the run divergent and continues by drawing fresh. Either way it is a new
run continuing the same history, announced — never a different run wearing the word
"replay".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Self, cast

from pydantic import BaseModel

from anagrapheus.timeline._canonical import Json, concrete_json
from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.pins import PinKey, PinPromoted, ReplayDiverged
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from anagrapheus.timeline.registry import EventTypeRegistry
    from anagrapheus.timeline.store import EventStore
    from anagrapheus.timeline.upcast import UpcastRegistry


__all__ = ["Divergence", "PinMiss", "ReplayPins"]

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
    """

    def __init__(
        self,
        pins: dict[str, Json],
        *,
        on_miss: Literal["strict", "record"] = "strict",
        announce: Callable[[Divergence], None] | None = None,
    ) -> None:
        if on_miss not in ("strict", "record"):
            # Literal annotations are not enforced at runtime; a typo must not
            # silently weaken strict replay into live draws.
            raise ValueError(f"on_miss must be 'strict' or 'record', got {on_miss!r}")
        self._pins = pins
        self._on_miss: Literal["strict", "record"] = on_miss
        self._announce = announce
        self._hits = 0
        self._divergence: Divergence | None = None

    @staticmethod
    def announce_to(
        store: EventStore, registry: EventTypeRegistry, *, unit_id: UUID
    ) -> Callable[[Divergence], None]:
        """
        An ``announce`` callback that appends a :class:`ReplayDiverged` event.

        With this wired, a record-mode run that stops being a replay says so in the
        authoritative log, not just in this object's memory.
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
        return cls(pins, on_miss=on_miss, announce=announce)

    @property
    def divergence(self) -> Divergence | None:
        """The first miss, if this replay has stopped being a replay."""
        return self._divergence

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
        mode announces the first miss, then pins the fresh draw, so replayed and
        fresh keys alike keep reuse-under-retry semantics.
        """
        if key in self._pins:
            self._hits += 1
            return interpret(cast("R", self._pins[key]))
        self._miss(key, kind)
        return interpret(self._record(key, effect()))

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
        if key in self._pins:
            self._hits += 1
            return interpret(cast("R", self._pins[key]))
        self._miss(key, kind)
        return interpret(self._record(key, await effect()))

    def _miss(self, key: PinKey, kind: str) -> None:
        """Apply the ``on_miss`` policy: raise, or announce the first and carry on."""
        miss = Divergence(missed_key=key, kind=kind, after_hits=self._hits)
        if self._on_miss == "strict":
            if self._divergence is None:
                self._divergence = miss
            raise PinMiss(miss)
        if self._divergence is None:
            self._divergence = miss
            if self._announce is not None:
                self._announce(miss)

    def _record[R: Json](self, key: PinKey, drawn: R) -> R:
        """Pin a fresh draw, so replayed and fresh keys alike keep reuse-under-retry."""
        raw = cast("R", concrete_json(drawn))
        self._pins[key] = cast("Json", raw)
        return raw
