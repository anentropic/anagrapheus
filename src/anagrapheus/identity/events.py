"""
The identity decisions, as event payloads.

Identity decisions are events rather than rows in a mutable side table, so the
registry that answers "which entity is this?" is a fold: rebuildable, disposable,
replayable and auditable. A bad merge is undone by superseding it, not by mutating a
table.

Be clear about what this buys. It makes identity decisions durable, auditable and
reversible. It does **not** make them automatic — something still has to judge that
``A. Smith`` is ``alice@corp``. The win is governance, not inference.

These are the *shapes*. An application that would rather keep its own vocabulary
registers its own types and binds them per role — see
:mod:`anagrapheus.identity.vocabulary`.
"""

# Design: note 02 (the shape of Split); upstream agent-core doc 00 §5c.

from __future__ import annotations

import unicodedata
from uuid import UUID

from pydantic import model_validator

from anagrapheus.identity.ladder import UNGRADED
from anagrapheus.timeline.envelope import DomainEvent

__all__ = [
    "Aliased",
    "Established",
    "Merged",
    "Split",
    "SplitAssignment",
    "normalize_surface_form",
]


def normalize_surface_form(surface_form: str) -> str:
    """
    Normalize a surface form for exact matching: Unicode NFC, nothing else.

    NFC is a claim about *representation* — two encodings of the same character are
    the same character. Case folding, whitespace collapsing and accent stripping are
    claims about *meaning* ("is ``Alice`` the same name as ``alice``?"), which this
    layer is forbidden to make. An application that wants them normalizes before it
    gets here.
    """
    return unicodedata.normalize("NFC", surface_form)


class Established(DomainEvent, frozen=True):
    """
    The system is now tracking a thing it currently calls ``first_surface_form``.

    Not a claim about the world's ontology. Establishing mints an id where there is no
    external referent to match against, so resolution is not merely unnecessary here,
    it is meaningless.

    ``provenance`` and ``stance`` are both opaque to this library — strings the
    application defines, whose vocabularies are its own domain semantics.

    ``stance`` defaults to :data:`~anagrapheus.identity.ladder.UNGRADED` rather than to
    anything confident. For an *established* entity the system chose the name itself,
    so a top grading would be true; for a *resolved* one the first form is a claim like
    any other, and this layer cannot tell the two apart. Recording nothing is the only
    honest default; an application that knows which case it is in says so.
    """

    entity_id: UUID
    first_surface_form: str
    provenance: str
    stance: str = UNGRADED


class Aliased(DomainEvent, frozen=True):
    """
    Another surface form for an entity, at a stance.

    ``stance`` is an opaque token from the application's own vocabulary — the same
    rule as ``provenance``, and for the same reason. To have this layer *rank* by it,
    declare an :class:`~anagrapheus.identity.ladder.Ladder`; without one the token is
    stored and returned but never compared.
    """

    entity_id: UUID
    surface_form: str
    stance: str = UNGRADED


class Merged(DomainEvent, frozen=True):
    """
    Two ids turned out to name one referent; ``absorbed`` yields to ``kept``.

    A correction that appends rather than edits. ``source_event`` names the event that
    justified the decision, when one exists — the envelope's ``causation_id`` is the
    general mechanism, this is the specific evidence.
    """

    kept: UUID
    absorbed: UUID
    source_event: UUID | None = None

    @model_validator(mode="after")
    def _distinct(self) -> Merged:
        if self.kept == self.absorbed:
            raise ValueError("an entity cannot be merged into itself")
        return self


class SplitAssignment(DomainEvent, frozen=True):
    """One entity carved out of another by a :class:`Split`, and the forms it takes."""

    entity_id: UUID
    surface_forms: list[str]


class Split(DomainEvent, frozen=True):
    """
    One id turned out to name several referents.

    Each assignment mints a new entity and names the surface forms that move to it;
    forms not reassigned stay with ``original``, which survives the split.

    The upstream design names this event and leaves its fields undefined. This shape is
    anagrapheus's own.
    """

    original: UUID
    into: list[SplitAssignment]
    source_event: UUID | None = None

    @model_validator(mode="after")
    def _coherent(self) -> Split:
        if not self.into:
            raise ValueError("a split must name at least one new entity")
        ids = [assignment.entity_id for assignment in self.into]
        if self.original in ids:
            raise ValueError("a split's new entities must all differ from the original")
        if len(ids) != len(set(ids)):
            raise ValueError("a split's new entities must be distinct")
        forms = [
            normalize_surface_form(form)
            for assignment in self.into
            for form in assignment.surface_forms
        ]
        if len(forms) != len(set(forms)):
            # A split exists to disambiguate; sending one form to two of its products
            # asks for the opposite, and would resolve by assignment order.
            raise ValueError("a split must not assign one surface form to two entities")
        return self
