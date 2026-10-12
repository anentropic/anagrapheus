"""
The library's own belief events, and how the fold reads them.

Three events, one per AGM operation, for an application whose domain has no event that
already carries the belief — a seeded goal, a conclusion a model wrote, a re-key. An
application whose events *do* carry it (an observation, a remark overheard) binds its
own types to an :data:`~anagrapheus.beliefs.ops.Interpreter` instead and never emits
these: doc 00 §8's rule, *the library ships the shape, the application owns the
name*.

**They replace ``BeliefMinted``**, which recorded that an id was minted and nothing
about what was believed under it. The value, the stance and the holder went straight
into doxastica, so dropping the belief store lost them and doc 00 §2's one load-bearing
rule — a layer that cannot be dropped and rebuilt has escaped its station — was broken
by the layer's own front door. Minting needs no event of its own: it is a pure function
of the predicate and the entities (note 03 §3), so the fold mints as it applies and
writes the index row directly.

The payload names a belief by predicate and entities, never by id (doc 00 §6a), and a
stance by its name, which is doxastica's own wire form for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID  # noqa: TC003 -- pydantic resolves event annotations at runtime

from doxastica import EdgeType, Stance
from pydantic import BaseModel, JsonValue

from anagrapheus.beliefs.ops import Contract, Edge, Expand, Revise, StateRef
from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from anagrapheus.beliefs.ops import BeliefOp, Interpreter
    from anagrapheus.timeline.envelope import Envelope
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = [
    "BUILTIN_INTERPRETERS",
    "BeliefContracted",
    "BeliefExpanded",
    "BeliefRevised",
    "EdgeRecord",
    "register_builtin_events",
    "stance_name",
]

#: A stance's wire form: its name. ``Stance(3)`` is ``certain`` and ``Stance("certain")``
#: is an error, so the name is the only spelling that survives a round trip.
type StanceName = Literal["doubted", "suspected", "believed", "certain"]

_NAMES: Final[Mapping[Stance, StanceName]] = {
    Stance.doubted: "doubted",
    Stance.suspected: "suspected",
    Stance.believed: "believed",
    Stance.certain: "certain",
}


def stance_name(stance: Stance) -> StanceName:
    """``stance`` in its wire form."""
    return _NAMES[stance]


class EdgeRecord(BaseModel, frozen=True):
    """An :class:`~anagrapheus.beliefs.ops.Edge`, as the log carries it."""

    scope_id: str
    belief_id: str
    event_id: UUID
    type: EdgeType = EdgeType.DERIVED_FROM

    @classmethod
    def of(cls, edge: Edge) -> EdgeRecord:
        """``edge`` in the form an event payload carries."""
        to = edge.to
        return cls(
            scope_id=to.scope_id, belief_id=to.belief_id, event_id=to.event_id, type=edge.type
        )

    def edge(self) -> Edge:
        """Back to the operation's form."""
        return Edge(StateRef(self.scope_id, self.belief_id, self.event_id), self.type)


class BeliefRevised(DomainEvent, frozen=True):
    """``scope_id`` comes to hold ``value`` for this proposition, superseding what it held."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]
    value: JsonValue
    stance: StanceName = "certain"
    edges: tuple[EdgeRecord, ...] = ()


class BeliefExpanded(DomainEvent, frozen=True):
    """``scope_id`` comes to hold ``value`` for this proposition, without revision."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]
    value: JsonValue
    stance: StanceName = "certain"
    edges: tuple[EdgeRecord, ...] = ()


class BeliefContracted(DomainEvent, frozen=True):
    """``scope_id`` stops holding this proposition."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]


def _revised(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    if not isinstance(event, BeliefRevised):
        raise TypeError(f"expected BeliefRevised, got {type(event).__name__}")
    return (
        Revise(
            event.scope_id,
            event.predicate,
            event.entity_ids,
            event.value,
            Stance[event.stance],
            tuple(record.edge() for record in event.edges),
        ),
    )


def _expanded(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    if not isinstance(event, BeliefExpanded):
        raise TypeError(f"expected BeliefExpanded, got {type(event).__name__}")
    return (
        Expand(
            event.scope_id,
            event.predicate,
            event.entity_ids,
            event.value,
            Stance[event.stance],
            tuple(record.edge() for record in event.edges),
        ),
    )


def _contracted(_envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    if not isinstance(event, BeliefContracted):
        raise TypeError(f"expected BeliefContracted, got {type(event).__name__}")
    return (Contract(event.scope_id, event.predicate, event.entity_ids),)


_MODELS: Final[Mapping[str, type[DomainEvent]]] = {
    "BeliefRevised": BeliefRevised,
    "BeliefExpanded": BeliefExpanded,
    "BeliefContracted": BeliefContracted,
}

#: How the fold reads the library's own belief events, keyed ``(namespace, type)``.
#: Installed by default; an application adds its own beside them.
BUILTIN_INTERPRETERS: Final[Mapping[tuple[str, str], Interpreter]] = {
    (RESERVED_NAMESPACE, "BeliefRevised"): _revised,
    (RESERVED_NAMESPACE, "BeliefExpanded"): _expanded,
    (RESERVED_NAMESPACE, "BeliefContracted"): _contracted,
}


def register_builtin_events(registry: EventTypeRegistry) -> None:
    """Register this library's own belief events under the reserved namespace."""
    for name, model in _MODELS.items():
        registry._register(EventKey(RESERVED_NAMESPACE, name, 1), model)  # noqa: SLF001
