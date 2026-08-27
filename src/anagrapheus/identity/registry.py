"""
The registry: what the folded identity decisions add up to.

Read-only. Every question here is answered from projection state that the log can
rebuild, and no method interprets what a surface form *means* — that boundary is the
layer's whole discipline. What this buys is governance: decisions that are durable,
auditable and reversible. What it does not buy is inference, and an API that implied
otherwise would be lying about which of the two it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from anagrapheus.identity.errors import AmbiguousSurfaceForm, MergeCycle, UnknownEntity
from anagrapheus.identity.events import normalize_surface_form

if TYPE_CHECKING:
    from uuid import UUID

    from anagrapheus.identity.ladder import Ladder
    from anagrapheus.identity.store import AliasRow, DecisionRow, IdentityStore, RekeyRow

__all__ = ["Candidate", "IdentityRegistry", "Resolution"]


class Candidate(NamedTuple):
    """One entity a surface form might name, at the stance recorded for that form."""

    entity_id: UUID
    stance: str
    seq: int


class Resolution(NamedTuple):
    """
    What a surface form matched — candidates, not an answer.

    Deliberately not ``UUID | None``. Resolution is a judgment about the world, and
    something other than this layer has to make it; returning a bare id would dress a
    lookup up as a decision.
    """

    surface_form: str
    candidates: tuple[Candidate, ...]

    def sole(self) -> UUID:
        """
        The single candidate, or raise.

        For the common case where the caller has already established that a form is
        unambiguous. It raises rather than guessing, so that assumption stays visible.
        """
        if len(self.candidates) != 1:
            raise AmbiguousSurfaceForm(
                f"{self.surface_form!r} matched {len(self.candidates)} entities"
            )
        return self.candidates[0].entity_id


class IdentityRegistry:
    """
    Queries over the folded identity decisions.

    A ``ladder`` is the application's own ordinal vocabulary for alias stance. Supply
    one and candidates come back strongest first, ranked in the query; omit it and they
    come back in ``seq`` order, because this layer has no opinion about which of two
    stance tokens is stronger.
    """

    def __init__(self, store: IdentityStore, *, ladder: Ladder | None = None) -> None:
        self._store = store
        self._ladder = ladder

    def resolve(self, surface_form: str) -> Resolution:
        """
        Every entity this surface form is attached to, canonicalized and deduped.

        Matching is **exact** on the NFC-normalized form. There is no fuzzy matching
        here and there will not be: deciding that ``A. Smith`` is ``alice@corp`` is a
        claim about the world, and this layer's contract is to record such claims, not
        to make them.

        Candidates whose entities have since merged collapse into one. With a ladder
        that keeps the strongest stance recorded for the form; without one it keeps the
        earliest, since nothing here can rank two tokens.
        """
        normalized = normalize_surface_form(surface_form)
        best: dict[UUID, Candidate] = {}
        # The store returns rows in the order the ranking asks for, so the first row
        # seen for a canonical entity is already the one to keep.
        for row in self._store.candidates(normalized, ladder=self._ladder):
            entity_id = self.canonical(row.entity_id)
            best.setdefault(entity_id, Candidate(entity_id, row.stance, row.seq))
        return Resolution(surface_form=normalized, candidates=tuple(best.values()))

    def canonical(self, entity_id: UUID) -> UUID:
        """
        Follow an entity's merge chain to the id that survives.

        Raises :class:`~anagrapheus.identity.errors.MergeCycle` if the chain returns to
        where it started — a contradiction in the log, correctable only by superseding
        one of the merges.
        """
        seen = {entity_id}
        current = entity_id
        while True:
            row = self._store.entity(current)
            if row is None or row.merged_into is None:
                return current
            current = row.merged_into
            if current in seen:
                raise MergeCycle(f"merge chain from {entity_id} cycles at {current}")
            seen.add(current)

    def surface_forms(self, entity_id: UUID) -> list[AliasRow]:
        """Every surface form attached to an entity, as recorded."""
        return self._store.aliases(entity_id)

    def history(self, entity_id: UUID) -> list[DecisionRow]:
        """
        The entity's audit trail, oldest first.

        The reason the decisions are events: every establish, alias, merge and split
        that touched this entity, in order, with the ``seq`` that anchors each to the
        stream.
        """
        row = self._store.entity(entity_id)
        if row is None:
            raise UnknownEntity(f"no entity established with id {entity_id}")
        return self._store.decisions(entity_id)

    def rekeys_since(self, seq: int) -> list[RekeyRow]:
        """
        Identity decisions above ``seq`` that invalidate keys built on an entity.

        The seam to the belief layer. A merge means every belief keyed on the absorbed
        entity is now suspect; feeding these to a retraction cascade returns exactly
        which states need re-keying. This layer states the consequence and performs no
        cascade of its own — it does not know what a belief is.
        """
        return self._store.rekeys_since(seq)
