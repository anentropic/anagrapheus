"""Identity error types."""

from __future__ import annotations

__all__ = [
    "AlreadyMerged",
    "AmbiguousSurfaceForm",
    "IdentityError",
    "MergeCycle",
    "NotMerged",
    "UnknownEntity",
]


class IdentityError(Exception):
    """Base class for all identity errors."""


class MergeCycle(IdentityError):
    """
    Following an entity's merge chain returned to where it started.

    A cycle is a contradiction in the log: the entities involved have no canonical id
    until an :class:`~anagrapheus.identity.events.Unmerged` withdraws one of the
    merges. Raised rather than looping, because the alternative is a query that hangs.

    Also raised before append by :func:`~anagrapheus.identity.decisions.merge` when it
    is given the registry and the merge would close a cycle.
    """


class AlreadyMerged(IdentityError):
    """
    A merge named an absorbed entity that already yields to another.

    Raised before append by :func:`~anagrapheus.identity.decisions.merge` when it is
    given the registry and ``absorbed`` is already merged, into ``kept`` or anywhere
    else. An entity yields to one survivor at a time: unmerge it first, then merge it
    where it belongs.
    """


class NotMerged(IdentityError):
    """
    An unmerge named a pair that is not merged.

    Raised before append by :func:`~anagrapheus.identity.decisions.unmerge` when
    ``absorbed`` is not currently merged directly into ``kept``. An unmerge withdraws
    one merge, so there has to be that merge to withdraw.
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
