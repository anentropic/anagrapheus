"""
An application's ordinal vocabulary for how firmly a surface form is held.

The identity layer records a stance token on every alias and never interprets it —
what ``"believed"`` means, and whether it outranks ``"suspected"``, is domain
semantics this library must not own (the same rule that makes ``provenance`` an
opaque string). But ranking candidates is genuinely useful, so the application
declares its own ladder once and the layer orders by it without knowing what any
rung means.

Why a ladder of tokens rather than a number: NVM retired numeric confidence because a
scalar invites arithmetic over a quantity nothing defines, and because ranking advice
and epistemic state must stay separate. An integer column here would reproduce that at
the library boundary — two consumers could disagree about the direction and granularity
of the scale — and ranks are position-dependent, so inserting a rung would silently
re-grade every stored row. Tokens survive that; their order is declared, not encoded.
"""

# Design: note 02; upstream NVM R21 (stance replaces numeric confidence).

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["Ladder", "UNGRADED"]

# The stance of an alias nothing has graded. Ranks below every rung, and is what
# `Established` records unless the application says otherwise.
UNGRADED = ""


class Ladder:
    """
    A total order over stance tokens, weakest first.

    Comparison is the only thing it supplies: there is no arithmetic over rungs and
    no distance between them, so nothing can average two stances into a third.

    A token that is not a rung — a vocabulary the log carried before this ladder was
    declared, or :data:`UNGRADED` — ranks below all of them rather than raising. The
    log is the authority and may legitimately hold tokens this process has never
    heard of; refusing to read them would make an old log unopenable.
    """

    def __init__(self, rungs: Sequence[str]) -> None:
        ordered = tuple(rungs)
        if len(ordered) != len(set(ordered)):
            raise ValueError(f"ladder rungs must be distinct: {ordered}")
        if UNGRADED in ordered:
            raise ValueError("the empty token is reserved for an ungraded alias")
        self._rungs = ordered

    @property
    def rungs(self) -> tuple[str, ...]:
        """The declared vocabulary, weakest first."""
        return self._rungs

    def rank(self, stance: str) -> int:
        """Position on the ladder; ``-1`` for a token this ladder does not declare."""
        try:
            return self._rungs.index(stance)
        except ValueError:
            return -1

    def __repr__(self) -> str:
        return f"Ladder({self._rungs!r})"
