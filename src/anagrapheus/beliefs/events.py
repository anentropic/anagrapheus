"""
Minting a belief id, as an event payload.

Minting is deterministic, so — unlike an identity decision — nothing is *recorded*
here that could not be recomputed. What the event buys is the index: doxastica can
filter by ``belief_id`` but nothing in its seam can look *inside* one, so
"which beliefs mention this entity?" has no answer anywhere in the stack. That
question is exactly what identity's re-key seam asks after a merge, and the index that
answers it has to be folded from somewhere. The log is the authority, so it is folded
from here.

Recording it also makes the set of minted ids auditable, which doc 00 §6b wants as a
*secondary* artifact. It is secondary here too: the id carries its own components
(see :mod:`anagrapheus.beliefs.predicates`), so the index can be dropped and re-folded
and nothing is lost with it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import model_validator

from anagrapheus.timeline.envelope import DomainEvent

__all__ = ["BeliefMinted"]


class BeliefMinted(DomainEvent, frozen=True):
    """
    A belief id was minted for ``predicate`` over ``entity_ids``.

    The event carries the components as well as the id, rather than the id alone. They
    are recoverable by parsing, but a fold that re-derives them would have to hold a
    predicate registry to do it — and the registry is application code that may have
    changed since. Carrying them keeps the fold a pure write.
    """

    belief_id: str
    predicate: str
    entity_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def _check(self) -> BeliefMinted:
        if not self.belief_id:
            raise ValueError("belief_id must not be empty")
        if not self.entity_ids:
            raise ValueError(f"belief {self.belief_id!r} names no entities")
        return self
