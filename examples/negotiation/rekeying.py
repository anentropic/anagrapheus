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

from collections import defaultdict
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
    # A *list* per belief id, not one plan. `plan_rekeys` emits a plan per identity
    # decision, so a proposition naming two independently re-keyed entities yields two
    # plans sharing an old id -- and a dict keyed on that id keeps only the last,
    # leaving the other entity reference stale. Chaining them applies both.
    by_id: dict[str, list[BeliefRekey]] = defaultdict(list)
    for plan in plans:
        by_id[plan.belief_id].append(plan)

    applied: list[Applied] = []
    for scope in scopes if scopes is not None else table.scopes():
        moved = 0
        undecided: list[str] = []
        for state in list(table.core.query_scope(scope, BeliefFilter())):
            chain = by_id.get(state.belief_id)
            if chain is None:
                continue
            successor = _successor(chain, state.value, choose, table)
            if successor is None:
                undecided.append(state.belief_id)
                continue
            # As events, carrying the original stance, and never on the store directly.
            # A write to the core would drop the stance to its default, reach neither the
            # entity index nor the log, and vanish on the next rebuild -- so a second
            # merge could not plan a re-key for it, and a rebuilt store would hold the
            # belief under the absorbed id it was moved away from.
            parsed = table.predicates.parse(successor)
            old = table.predicates.parse(state.belief_id)
            # One batch, survivor first: the two events commit together or not at all,
            # and within it the survivor is folded before the original is retracted.
            table.append(
                table.events.revise(
                    scope,
                    parsed.name,
                    parsed.entity_ids,
                    state.value,
                    stance=state.stance,
                    causation_id=source_event,
                ),
                table.events.contract(scope, old.name, old.entity_ids, causation_id=source_event),
            )
            moved += 1
        if moved or undecided:
            applied.append(Applied(scope, moved, tuple(undecided)))
    return applied


def _successor(
    chain: Sequence[BeliefRekey],
    value: Any,
    choose: Callable[[BeliefRekey, Any], str | None] | None,
    table: Table,
) -> str | None:
    """
    The id this belief becomes after every plan naming it.

    ``None`` if one of them needed a judgment call and no chooser was given.

    Each plan substitutes one entity reference, so a proposition naming two
    independently re-keyed entities needs both applied. Rather than chain the plans --
    where each would have to see the previous one's output -- every plan is diffed
    against the *original* id to yield one entity substitution, and the substitutions
    are applied together. Order stops mattering, which is what makes it correct.
    """
    original = table.predicates.parse(chain[0].belief_id)
    substitutions: dict[UUID, UUID] = {}
    for plan in chain:
        if len(plan.candidates) == 1:
            successor = plan.candidates[0]
        else:
            picked = choose(plan, value) if choose is not None else None
            if picked is None:
                return None
            successor = picked
        after = table.predicates.parse(successor)
        substitutions.update(
            (before, now)
            for before, now in zip(original.entity_ids, after.entity_ids, strict=True)
            if before != now
        )
    moved = tuple(substitutions.get(entity, entity) for entity in original.entity_ids)
    return table.predicates.belief_id(original.name, *moved)
