"""
Minting a belief id, ready to append.

Returns a ``PendingEvent`` rather than appending one, for the same reason the identity
helpers do: a belief is minted *because* something happened, and the caller owns that
batch and its transaction.

The mint itself is :meth:`PredicateRegistry.belief_id
<anagrapheus.beliefs.predicates.PredicateRegistry.belief_id>` and needs nothing from
this module. What this adds is the record — the event whose fold gives the index that
identity's re-key seam needs on the other side of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anagrapheus.beliefs.events import BeliefMinted

if TYPE_CHECKING:
    from uuid import UUID

    from anagrapheus.beliefs.predicates import PredicateRegistry
    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["mint"]


def mint(
    types: EventTypeRegistry,
    predicates: PredicateRegistry,
    predicate: str,
    *entity_ids: UUID,
    correlation_id: UUID | None = None,
) -> tuple[str, PendingEvent]:
    """
    Mint a belief id and the event that records it.

    Minting the same proposition twice is ordinary rather than an error — the same
    thing can be believed in two scopes, or re-asserted later — and it is
    deterministic, so both mints produce the same id and the fold's write is an
    upsert. Appending the event twice costs a row in the log and changes nothing in
    the index.
    """
    minted = predicates.mint(predicate, *entity_ids)
    belief_id = predicates.render(minted)
    event = BeliefMinted(
        belief_id=belief_id,
        predicate=predicate,
        entity_ids=minted.entity_ids,
    )
    return belief_id, types.pending(event, correlation_id=correlation_id)
