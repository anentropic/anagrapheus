"""
The belief index's query surface.

The one question the rest of the stack cannot answer: **which beliefs mention this
entity?** doxastica's ``BeliefFilter`` is closed over four fields and can filter *by* a
belief id, but nothing in its seam looks inside one; the identity layer knows entities
and has never heard of a belief. So the join between them lives here, and without it
identity's re-key seam has no other side.

This is a projection over what was minted, not over what is believed. A belief id in
the index says an id was minted, never that anything currently holds a belief under
it — that is doxastica's question, asked with the id this index hands back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from anagrapheus.beliefs.store import BeliefIndexStore, BeliefRow

__all__ = ["BeliefIndex"]


class BeliefIndex:
    """Reads the belief index the fold maintains."""

    def __init__(self, store: BeliefIndexStore) -> None:
        self._store = store

    def beliefs_for(self, entity_id: UUID) -> Sequence[BeliefRow]:
        """
        Every belief id naming ``entity_id``, oldest first.

        The re-key seam's input. A belief naming the entity twice — ``sibling(a, a)``,
        say — appears once.
        """
        return self._store.beliefs_for(entity_id)

    def beliefs_of(self, predicate: str) -> Sequence[BeliefRow]:
        """Every belief id minted for ``predicate``, oldest first."""
        return self._store.beliefs_of(predicate)

    def entities_of(self, belief_id: str) -> tuple[UUID, ...]:
        """
        The entities ``belief_id`` names, in argument order, or ``()`` if unknown.

        Empty rather than raising: the index is a fold over a range of the log, so an
        id it has not seen is an ordinary answer — the mint may be above the position,
        or in another application's namespace entirely.
        """
        row = self._store.belief(belief_id)
        return () if row is None else row.entity_ids
