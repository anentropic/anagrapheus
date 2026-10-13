"""
Where two holders disagree.

The payoff of minting belief ids by rule, shipped rather than quoted: three lines of
query, and the whole multi-agent-disagreement case — *but only if both scopes minted
their ids through the same registry*. That condition is what
:mod:`anagrapheus.beliefs.writer` exists to make structural rather than aspirational.

The comparison is on values, and values are opaque to doxastica and to this library
alike. Two holders "disagree" here when they hold different values under the same
belief id; whether that difference *matters* is the application's reading of its own
values.
"""

# Design: note 03; upstream agent-core doc 00 §6c.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from doxastica import BeliefFilter

if TYPE_CHECKING:
    from doxastica import BeliefStore

__all__ = ["Disagreement", "divergence"]


class Disagreement(NamedTuple):
    """One belief two scopes hold differently."""

    belief_id: str
    held: Any
    reference: Any


def divergence(store: BeliefStore, holder: str, reference: str) -> list[Disagreement]:
    """
    What ``holder`` believes that disagrees with ``reference``.

    Only beliefs *both* scopes hold are compared. A belief one holds and the other has
    never had is not a disagreement — it is an absence, and treating the two alike
    would report every gap in a holder's knowledge as a contradiction.
    """
    held = {s.belief_id: s for s in store.query_scope(holder, BeliefFilter())}
    known = {s.belief_id: s for s in store.query_scope(reference, BeliefFilter())}
    # Sorted, because the intersection of two key views has no order and a query whose
    # results shuffle between runs is one nobody can write a test or a diff against.
    return [
        Disagreement(belief_id, held[belief_id].value, known[belief_id].value)
        for belief_id in sorted(held.keys() & known.keys())
        if held[belief_id].value != known[belief_id].value
    ]
