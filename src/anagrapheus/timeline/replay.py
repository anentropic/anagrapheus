"""
Generation-replay: rerun a unit of work from its promoted pins.

Same log, same code, same prompt assembly -> same run. Change the prompt assembly and
the keys miss — and a miss is detectable: strict mode raises at the first miss, record
mode marks the run divergent and continues by drawing fresh. Either way it is a new
run continuing the same history, announced — never a different run wearing the word
"replay".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Self, cast

from pydantic import BaseModel, ConfigDict

from anagrapheus.timeline._canonical import Json
from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.pins import PinKey, PinPromoted
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["Divergence", "PinMiss", "ReplayPins"]

type ExtractFn = Callable[[StoredEvent, DomainEvent], tuple[PinKey, Json] | None]


class Divergence(BaseModel):
    """Where a replay stopped being a replay: the first key with no pinned result."""

    model_config = ConfigDict(frozen=True)

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

    Implements the same ``PinResolver`` seam as the live ledger, so a unit of work is
    written once and replays without modification. ``on_miss`` picks the posture:
    ``"strict"`` raises :class:`PinMiss`; ``"record"`` marks the divergence at the
    first miss and falls through to the effect — the run continues, announced as new.
    """

    def __init__(
        self,
        pins: dict[str, Json],
        *,
        on_miss: Literal["strict", "record"] = "strict",
    ) -> None:
        self._pins = pins
        self._on_miss: Literal["strict", "record"] = on_miss
        self._hits = 0
        self._divergence: Divergence | None = None

    @classmethod
    def from_events(
        cls,
        events: Iterable[StoredEvent],
        registry: EventTypeRegistry,
        *,
        unit_id: UUID | None = None,
        extract: ExtractFn | None = None,
        on_miss: Literal["strict", "record"] = "strict",
    ) -> Self:
        """
        Build a replay source from a stream (or fixture) of stored events.

        The default understands :class:`PinPromoted`; applications that promoted pins
        into their own vocabulary supply ``extract`` — the mirror of ``promote_as``.
        ``unit_id`` narrows to one unit of work; omitted, every pin in the stream
        loads (later pins win on key collision).
        """
        pins: dict[str, Json] = {}
        for stored in events:
            env = stored.envelope
            if extract is not None:
                key = EventKey(env.namespace, env.type, env.schema_version)
                decoded = registry.decode(key, stored.payload)
                pair = extract(stored, decoded)
                if pair is not None:
                    pins[pair[0]] = pair[1]
                continue
            if (env.namespace, env.type) != (RESERVED_NAMESPACE, "PinPromoted"):
                continue
            promoted = registry.decode(
                EventKey(env.namespace, env.type, env.schema_version), stored.payload
            )
            assert isinstance(promoted, PinPromoted)
            if unit_id is not None and promoted.unit_id != unit_id:
                continue
            pins[promoted.key] = cast("Json", promoted.result)
        return cls(pins, on_miss=on_miss)

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
        """Answer from the pinned result for ``key``; on a miss, follow ``on_miss``."""
        if key in self._pins:
            self._hits += 1
            return interpret(cast("R", self._pins[key]))
        if self._divergence is None:
            self._divergence = Divergence(missed_key=key, kind=kind, after_hits=self._hits)
            if self._on_miss == "strict":
                raise PinMiss(self._divergence)
        return interpret(effect())
