"""Identity error types."""

from __future__ import annotations

__all__ = [
    "AlreadyMerged",
    "AmbiguousSurfaceForm",
    "IdentityError",
    "MergeCycle",
    "NotMerged",
    "OutOfOrderDecision",
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
    is given the registry and the merge would close a cycle. The fold ignores such a
    merge, whose survivor already yields, so a cycle is only found in a projection that
    was folded before it did and has not been rebuilt since.
    """


class AlreadyMerged(IdentityError):
    """
    A merge named an entity that already yields to another.

    Raised before append by :func:`~anagrapheus.identity.decisions.merge` when it is
    given the registry and ``absorbed`` is already merged, into ``kept`` or anywhere
    else: an entity yields to one survivor at a time, so unmerge it first, then merge
    it where it belongs. Raised too when ``kept`` is already merged: an entity that has
    yielded names nothing and can survive no merge, so merge into the entity it
    resolves to instead.

    The check reads the registry, so it cannot see a merge built earlier in the same
    batch and not yet appended. Nothing is lost when it misses one: the fold ignores
    such a merge and records it as ignored.
    """


class NotMerged(IdentityError):
    """
    An unmerge named a pair that is not merged.

    Raised before append by :func:`~anagrapheus.identity.decisions.unmerge` when
    ``absorbed`` is not currently merged directly into ``kept``. An unmerge withdraws
    one merge, so there has to be that merge to withdraw.
    """


class OutOfOrderDecision(IdentityError):
    """
    An identity store was handed a decision older than one it has already applied.

    Raised by :class:`~anagrapheus.identity.sqlite.SQLiteIdentityStore`. The store
    ranks what merges carried by their positions, which only holds when decisions
    arrive in log order, as the projector folds them. Re-applying a decision already
    applied is not out of order: it changes nothing.
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
