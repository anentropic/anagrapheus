"""
The belief index's storage port.

One write and the reads the index answers with. Small, because the index is a small
idea: it is the entity → belief-id direction that neither doxastica (which can filter
*by* a belief id but never look inside one) nor the identity layer (which knows
entities and not beliefs) provides.

Projection state, disposable by construction: :meth:`reset` empties it and
:class:`~anagrapheus.timeline.folds.Projector` re-folds the log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

__all__ = ["BeliefIndexStore", "BeliefRow"]


class BeliefRow(NamedTuple):
    """One minted belief id, with what it was minted from."""

    belief_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]
    seq: int


class BeliefIndexStore(Protocol):
    """The storage the belief index reads, and the fold writes."""

    def minted(self, row: BeliefRow) -> None:
        """
        Record a minted belief id and the entities it names.

        An upsert: the same proposition is minted every time any scope writes it.
        """
        ...

    def belief(self, belief_id: str) -> BeliefRow | None:
        """The row for ``belief_id``, or ``None`` if this range never minted it."""
        ...

    def beliefs_for(self, entity_id: UUID) -> Sequence[BeliefRow]:
        """Every belief id naming ``entity_id``, oldest first."""
        ...

    def beliefs_of(self, predicate: str) -> Sequence[BeliefRow]:
        """Every belief id minted for ``predicate``, oldest first."""
        ...

    def reset(self) -> None:
        """Empty the projection so it can be rebuilt from the log."""
        ...
