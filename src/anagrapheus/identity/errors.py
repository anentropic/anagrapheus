"""Identity error types."""

from __future__ import annotations

__all__ = [
    "AmbiguousSurfaceForm",
    "IdentityError",
    "MergeCycle",
    "UnknownEntity",
]


class IdentityError(Exception):
    """Base class for all identity errors."""


class MergeCycle(IdentityError):
    """
    Following an entity's merge chain returned to where it started.

    A cycle is a contradiction in the log, not a state to recover from: the entities
    involved have no canonical id until a corrective event supersedes one of the
    merges. Raised rather than looping, because the alternative is a query that hangs.
    """


class UnknownEntity(IdentityError):
    """An entity id that no ``Established`` event in the folded range introduced."""


class AmbiguousSurfaceForm(IdentityError):
    """
    A surface form resolved to something other than exactly one entity.

    Raised only by the explicit :meth:`~anagrapheus.identity.registry.Resolution.sole`
    convenience. The ordinary path returns candidates and leaves the judgment where it
    belongs — with the caller.
    """
