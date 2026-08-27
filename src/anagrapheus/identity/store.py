"""
The identity projection's storage port.

Four write methods — one per role — and the reads the registry answers with. Keeping
the writes role-shaped rather than table-shaped means the fold is a four-way dispatch
and every rule about *how* a decision lands (which aliases move, what a merge records
for the belief layer) lives in one adapter instead of being spread across the fold.

This is projection state, so it is disposable by construction: :meth:`reset` empties
it and :class:`~anagrapheus.timeline.folds.Projector` re-folds the log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from anagrapheus.identity.events import Aliased, Established, Merged, Split
    from anagrapheus.identity.ladder import Ladder

__all__ = ["AliasRow", "DecisionRow", "EntityRow", "IdentityStore", "RekeyRow"]


class EntityRow(NamedTuple):
    """One entity as the registry currently holds it."""

    entity_id: UUID
    established_seq: int
    provenance: str
    merged_into: UUID | None
    merged_seq: int | None


class AliasRow(NamedTuple):
    """One surface form attached to an entity, at the stance last recorded."""

    entity_id: UUID
    surface_form: str
    stance: str
    seq: int


class DecisionRow(NamedTuple):
    """One line of an entity's audit trail."""

    seq: int
    entity_id: UUID
    kind: str
    detail: str


class RekeyRow(NamedTuple):
    """
    An identity decision that invalidates keys built on ``from_entity``.

    The seam doc 00 §5d describes: a merge means every belief keyed on the absorbed
    entity is now suspect, and the belief layer's retraction cascade is what turns
    that into a list of states to re-key.

    A merge emits exactly one row, naming its target. A split emits one row **per new
    entity** — the target is a choice between them, and which one a given belief
    belongs to depends on what the belief says, which this layer is forbidden to
    interpret. Several rows sharing a ``from_entity`` at one ``seq`` is how that
    ambiguity is stated.
    """

    seq: int
    kind: str
    from_entity: UUID
    to_entity: UUID


class IdentityStore(Protocol):
    """Persistence for the identity registry's folded state."""

    def established(self, event: Established, seq: int) -> None:
        """Record a newly tracked entity and its first surface form."""
        ...

    def aliased(self, event: Aliased, seq: int) -> None:
        """Attach a surface form to an entity, replacing any stance already held."""
        ...

    def merged(self, event: Merged, seq: int) -> None:
        """Point the absorbed entity at the kept one and record the re-key."""
        ...

    def split(self, event: Split, seq: int) -> None:
        """Carve new entities out of one, moving the named forms and recording re-keys."""
        ...

    def entity(self, entity_id: UUID) -> EntityRow | None:
        """Return an entity, or ``None`` if nothing established it."""
        ...

    def candidates(self, surface_form: str, *, ladder: Ladder | None = None) -> list[AliasRow]:
        """
        Return every alias exactly matching a normalized surface form.

        With a ``ladder``, strongest stance first and ``seq`` breaking ties; without
        one, ``seq`` order and no ranking — the layer has no opinion about which of
        two tokens is stronger unless the application declares one.
        """
        ...

    def aliases(self, entity_id: UUID) -> list[AliasRow]:
        """Return every surface form attached to an entity."""
        ...

    def decisions(self, entity_id: UUID) -> list[DecisionRow]:
        """Return an entity's audit trail, oldest first."""
        ...

    def rekeys_since(self, seq: int) -> list[RekeyRow]:
        """Return re-keying consequences recorded above ``seq``, oldest first."""
        ...

    def reset(self) -> None:
        """Empty the projection, leaving its schema in place."""
        ...
