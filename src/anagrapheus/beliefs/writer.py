"""
Building belief events, checked before they reach the log.

doxastica's seam takes a ``belief_id: str``, so any string will do and the minting
discipline is honour-based. Doc 00 §6c's divergence query works *only* if every scope
minted through the same registry, which is the argument for making the registry the
only way to mint — so a caller holding a :class:`BeliefEvents` never types a belief id.

**It writes nothing.** Each method returns the event to append, and the belief appears
in doxastica when :class:`~anagrapheus.beliefs.fold.BeliefFold` folds it. An earlier
``BeliefWriter`` wrote straight into the store and returned a ``BeliefMinted`` to
append, which left the value, the stance and the holder in the one place that could
not be rebuilt (note 03 §9). Returning the event also keeps the rule every helper in
this library follows: a belief changes *because* something happened, and the caller
owns that batch and its transaction.

**The checks run here, before append**, because once an event is canon a refusal in the
fold is a projection stuck on it forever. Pass the event that caused the belief as
``causation_id``; it is the envelope's, so "why does this scope believe that" is a
question the log answers the way it answers every other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doxastica import Stance

from anagrapheus.beliefs.events import (
    BeliefContracted,
    BeliefExpanded,
    BeliefRevised,
    EdgeRecord,
    stance_name,
)
from anagrapheus.beliefs.fold import check
from anagrapheus.beliefs.ops import Contract, Expand, Revise

if TYPE_CHECKING:
    from uuid import UUID

    from anagrapheus.beliefs.ops import BeliefOp, Edge
    from anagrapheus.beliefs.predicates import PredicateRegistry
    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["BeliefEvents", "RelationalValue"]

#: What a relational belief's value may be: whether the proposition holds, and nothing
#: else. *How firmly* is a separate axis, and doxastica already has a parameter for it —
#: putting a grade in the value would merge the two, which is the merge R21 forbids and
#: which doxastica's own encoding refuses anyway (values are JSON; ``Stance`` is not).
type RelationalValue = bool


class BeliefEvents:
    """The library's belief events, minted through one registry and checked before append."""

    def __init__(self, types: EventTypeRegistry, predicates: PredicateRegistry) -> None:
        self._types = types
        self._predicates = predicates

    def check(self, *ops: BeliefOp) -> None:
        """
        Raise if :class:`~anagrapheus.beliefs.fold.BeliefFold` would refuse ``ops``.

        For an application's own belief-bearing events: run what its interpreter makes
        of the event through this before appending it.
        """
        check(self._predicates, ops)

    def revise(
        self,
        scope_id: str,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        *,
        stance: Stance = Stance.certain,
        edges: tuple[Edge, ...] = (),
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> PendingEvent:
        """AGM revision: supersede whatever ``scope_id`` held for this proposition."""
        self.check(Revise(scope_id, predicate, entity_ids, value, stance, edges))
        event = BeliefRevised(
            scope_id=scope_id,
            predicate=predicate,
            entity_ids=entity_ids,
            value=value,
            stance=stance_name(stance),
            edges=tuple(EdgeRecord.of(edge) for edge in edges),
        )
        return self._types.pending(event, causation_id=causation_id, correlation_id=correlation_id)

    def expand(
        self,
        scope_id: str,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        *,
        stance: Stance = Stance.certain,
        edges: tuple[Edge, ...] = (),
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> PendingEvent:
        """AGM expansion: add without consistency enforcement."""
        self.check(Expand(scope_id, predicate, entity_ids, value, stance, edges))
        event = BeliefExpanded(
            scope_id=scope_id,
            predicate=predicate,
            entity_ids=entity_ids,
            value=value,
            stance=stance_name(stance),
            edges=tuple(EdgeRecord.of(edge) for edge in edges),
        )
        return self._types.pending(event, causation_id=causation_id, correlation_id=correlation_id)

    def contract(
        self,
        scope_id: str,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        *,
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> PendingEvent:
        """AGM contraction: retract this proposition in ``scope_id``."""
        self.check(Contract(scope_id, predicate, entity_ids))
        event = BeliefContracted(scope_id=scope_id, predicate=predicate, entity_ids=entity_ids)
        return self._types.pending(event, causation_id=causation_id, correlation_id=correlation_id)
