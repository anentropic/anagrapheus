"""Belief-layer error types."""

from __future__ import annotations

__all__ = [
    "BeliefError",
    "MalformedBeliefId",
    "UnknownPredicate",
    "UnknownScheme",
    "WrongArity",
]


class BeliefError(Exception):
    """Base class for all belief-layer errors."""


class UnknownPredicate(BeliefError):
    """
    A predicate name the registry does not declare.

    Predicates are schema and live in code, so this is the import-time-ish failure doc
    00 §6b asks for: minting an undeclared predicate is a bug in the caller, not a
    reason to invent one on the fly.
    """


class WrongArity(BeliefError):
    """
    A predicate minted with the wrong number of entities.

    This is the functional/relational trap closed mechanically. ``knows/2`` minted at
    arity 1 would key the belief on ``knows(alice)`` alone, and revising the value from
    ``bob`` to ``carol`` would then *silently retract* the belief about bob. The
    declaration is what makes that unrepresentable rather than merely discouraged.
    """


class MalformedBeliefId(BeliefError):
    """A string that does not parse as a belief id minted by this registry."""


class UnknownScheme(BeliefError):
    """
    A belief id carrying a minting scheme this process does not know how to render.

    Raised rather than re-minting under the current scheme: an id in an unknown scheme
    is one this code cannot reproduce, and quietly substituting today's rule would
    orphan the history keyed on the old one — the failure a scheme version exists to
    make loud.
    """
