"""
Applying a re-key across every scope — the loop the library does not provide.

When two names turn out to be one party, :func:`~anagrapheus.beliefs.plan_rekeys`
computes what each belief id becomes. It stops there, deliberately: doc 00 §6 says
doxastica is *"consumed here rather than designed"*, and carrying a plan into a belief
store is ``doxastica.revise``.

**With one scope that boundary is invisible. With several it is a loop**, and this
module is that loop — written here because the library has nowhere to put it:

- ``plan_rekeys`` works from the entity→belief index, which records what was *minted*
  and is scope-free by construction. A plan therefore says "belief id X becomes Y" and
  cannot say "in which scopes".
- Nothing can enumerate the scopes either. ``MemoryCore`` has ``query_scope`` but no
  ``scopes()``, so the caller must already know who is at the table — which is why
  :meth:`~examples.negotiation.table.Table.scopes` derives them from the parties.

Every consumer with more than one holder writes this. That is usually the sign
something belongs in the library, and note 06 records the case rather than settling it:
the counter-argument is that the loop needs the scope list, the scope list is the
application's modelling choice, and a library helper would have to take it as an
argument anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from doxastica import BeliefFilter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from uuid import UUID

    from examples.negotiation.table import Table

    from anagrapheus.beliefs import BeliefRekey

__all__ = ["Applied", "apply_rekeys"]


class Applied(NamedTuple):
    """What one scope did with the plan."""

    scope: str
    moved: int
    #: Beliefs left alone because the plan offered more than one successor and this
    #: application declined to guess. Never silently dropped -- see `apply_rekeys`.
    undecided: tuple[str, ...] = ()


def apply_rekeys(
    table: Table,
    plans: Sequence[BeliefRekey],
    *,
    scopes: Iterable[str] | None = None,
    source_event: UUID,
    choose: Callable[[BeliefRekey, Any], str | None] | None = None,
) -> list[Applied]:
    """
    Carry ``plans`` into every scope that holds one of the beliefs they re-key.

    Two things this loop must decide that the plan deliberately does not:

    **Which scopes.** A plan says "belief id X becomes Y" and cannot say where, because
    it was computed from the scope-free entity index. A scope that never held the old
    belief is skipped rather than written with a default — an absence is not a
    disagreement, the same distinction :func:`~anagrapheus.beliefs.divergence` draws,
    and inventing a value would turn a gap in a holder's knowledge into a claim it
    never made.

    **Which successor, after a split.** ``BeliefRekey.sole()`` raises when a decision
    left several candidates, and that is the library being right: which product a
    belief belongs to depends on what the belief *says*, which neither the belief layer
    nor identity may interpret. So ``choose`` is the application's judgment, and
    without one an ambiguous belief is reported in ``undecided`` rather than guessed
    at or dropped.
    """
    by_id = {plan.belief_id: plan for plan in plans}
    applied: list[Applied] = []
    for scope in scopes if scopes is not None else table.scopes():
        moved = 0
        undecided: list[str] = []
        for state in list(table.core.query_scope(scope, BeliefFilter())):
            plan = by_id.get(state.belief_id)
            if plan is None:
                continue
            successor = _successor(plan, state.value, choose)
            if successor is None:
                undecided.append(state.belief_id)
                continue
            # Write the survivor, then retract the old id -- in that order, so a crash
            # between them leaves the belief present twice rather than absent.
            # Duplicated is recoverable by re-running; lost is not.
            table.core.revise(scope, successor, state.value, source_event_id=source_event)
            table.core.contract(scope, state.belief_id, source_event_id=source_event)
            moved += 1
        if moved or undecided:
            applied.append(Applied(scope, moved, tuple(undecided)))
    return applied


def _successor(
    plan: BeliefRekey, value: Any, choose: Callable[[BeliefRekey, Any], str | None] | None
) -> str | None:
    """The id this belief becomes, or ``None`` if only the application could say."""
    if len(plan.candidates) == 1:
        return plan.candidates[0]
    return choose(plan, value) if choose is not None else None
