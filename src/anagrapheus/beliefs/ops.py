"""
What one event says about belief: the operations a fold applies to the belief store.

The log is the authority and doxastica is a fold over it (doc 00 §2). An event that
changes what somebody believes is *interpreted* into a short sequence of these, and
:class:`~anagrapheus.beliefs.fold.BeliefFold` applies them. The library's own belief
events interpret to exactly one each; an application's events (an observation, a
remark, an inference) interpret to as many as the event implies.

**The operations name a belief by predicate and entities, never by id.** Doc 00 §6a's
mint-from-structured-data rule, kept at the seam where it would be easiest to break: an
id is derived by the predicate registry the fold holds, so an interpreter cannot mint
around it.

**There is no source on an operation.** A belief's ``source_event_id`` is always the id
of the event being folded. That is what makes the fold idempotent — a state carrying
this event's id in this scope means the operation already ran — and it makes
doxastica's as-of order the log's order. *Why* the event happened is the envelope's
``causation_id``, which is where every other layer of this library already looks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doxastica import EdgeType, Stance

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from doxastica import BeliefState

    from anagrapheus.timeline.envelope import DomainEvent, Envelope

__all__ = [
    "BeliefOp",
    "Contract",
    "Edge",
    "Expand",
    "Interpreter",
    "Revise",
    "StateRef",
]


@dataclass(frozen=True, slots=True)
class StateRef:
    """
    One belief state, named by what produced it: a scope, a belief id and an event.

    A doxastica ``state_id`` is minted by the store and differs on every rebuild, so an
    edge recorded against one could never be replayed. This triple is stable — the
    event id is the state's ``source_event_id``, which the fold sets — and resolves to
    the same state on every fold of the same log.

    It takes the belief id rather than a predicate and entities, which looks like an
    exception to the rule above and is not: the rule governs *minting*, and a reference
    names a state that was already minted and written.
    """

    scope_id: str
    belief_id: str
    event_id: UUID

    @classmethod
    def of(cls, state: BeliefState) -> StateRef:
        """The reference that resolves back to ``state`` on this fold and every rebuild."""
        return cls(state.scope_id, state.belief_id, state.source_event_id)


@dataclass(frozen=True, slots=True)
class Edge:
    """
    A dependency from the state an operation writes to an existing one.

    ``DERIVED_FROM`` by default, because that is what a conclusion resting on an
    observation is: retract the observation and ``get_impact`` reports the conclusion.
    The direction is dependent -> source, the one ``get_impact`` walks against.
    """

    to: StateRef
    type: EdgeType = EdgeType.DERIVED_FROM


@dataclass(frozen=True, slots=True)
class Revise:
    """AGM revision: supersede whatever ``scope_id`` held for this proposition."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]
    value: Any
    stance: Stance = Stance.certain
    edges: tuple[Edge, ...] = ()


@dataclass(frozen=True, slots=True)
class Expand:
    """AGM expansion: add without consistency enforcement."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]
    value: Any
    stance: Stance = Stance.certain
    edges: tuple[Edge, ...] = ()


@dataclass(frozen=True, slots=True)
class Contract:
    """AGM contraction: retract this proposition in ``scope_id``. Vacuous if not held."""

    scope_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]


type BeliefOp = Revise | Expand | Contract

#: Reads one decoded event as the belief operations it implies.
#:
#: **Must be a pure function of the envelope and the payload.** It runs on every fold,
#: including a rebuild long after the event was written, so anything it consults that
#: moves — the identity layer's merge chain above all — would make a rebuilt store
#: differ from the incremental one. Resolve such things when the event is *written*, and
#: carry the result in the payload.
type Interpreter = Callable[[Envelope, DomainEvent], Sequence[BeliefOp]]
