"""Belief-layer error types."""

from __future__ import annotations

__all__ = [
    "BeliefError",
    "MalformedBeliefId",
    "MergeCycleInBatch",
    "UnknownPredicate",
    "UnknownScheme",
    "UnrenderableBelief",
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


class UnrenderableBelief(BeliefError):
    """
    A belief id in the index that this registry cannot re-mint, so a re-key cannot reach it.

    Its own type rather than whichever error the attempt happened to raise: a scheme
    this code does not know, an id that does not parse, and a predicate this registry
    does not declare are a migration problem, corrupt data and a mis-configured process
    respectively, and they want different responses. The original is kept as the
    ``__cause__`` so the distinction survives.
    """


class MergeCycleInBatch(BeliefError):
    """
    A batch of re-key rows follows a ring rather than a chain.

    Identity detects a merge cycle at read and refuses to name a canonical id; the
    planner refuses for the same reason. A ring has no last element, so there is no
    successor to re-mint to — and picking an arbitrary point in it would hand the
    caller a confident answer to a question the log contradicts itself about.
    """
