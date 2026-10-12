"""The re-key seam: what a merge computes, what a split refuses to guess."""

from __future__ import annotations

import uuid

import pytest

from anagrapheus.beliefs import (
    BeliefError,
    BeliefRow,
    UnknownPredicate,
    UnrenderableBelief,
    plan_rekeys,
)
from anagrapheus.identity.store import RekeyRow

from .conftest import Rig, build_predicates


def test_a_merge_computes_one_successor_per_affected_belief(rig: Rig):
    """
    §5d's payoff, executed rather than promised.

    The absorbed entity yielded to a single survivor, so every belief keyed on it has
    exactly one successor — computed from the id's own components, with nothing
    guessed and nothing consulted.
    """
    kept, absorbed, other = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(
        rig.asserted("location", absorbed),
        rig.asserted("knows", absorbed, other),
        rig.asserted("location", other),
    )

    plans = plan_rekeys(
        [RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=kept)],
        rig.index,
        rig.predicates,
    )

    assert [p.sole() for p in plans] == [
        rig.predicates.belief_id("location", kept),
        rig.predicates.belief_id("knows", kept, other),
    ]
    # The belief about an uninvolved entity is not in the plan at all.
    assert all(rig.predicates.belief_id("location", other) != p.belief_id for p in plans)


def test_a_split_yields_a_candidate_per_product_and_refuses_to_choose(rig: Rig):
    """
    A split states the set and refuses to pick from it.

    Which product a belief belongs to depends on what the belief *says* — and neither
    identity nor this layer may interpret that. Several candidates state exactly what
    is known; ``sole()`` raises rather than picking the first.
    """
    original, uk, us = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("employer", original))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="split", from_entity=original, to_entity=uk),
            RekeyRow(seq=9, kind="split", from_entity=original, to_entity=us),
        ],
        rig.index,
        rig.predicates,
    )

    assert len(plans) == 1
    assert plans[0].candidates == (
        rig.predicates.belief_id("employer", uk),
        rig.predicates.belief_id("employer", us),
    )
    with pytest.raises(ValueError, match="2 candidate successors"):
        plans[0].sole()


def test_the_rows_of_one_split_are_one_plan_not_several(rig: Rig):
    """
    Identity reports a split as several rows sharing a ``from_entity``.

    Planning each row alone would report it as several independent one-target re-keys,
    each of which ``sole()`` would happily answer — turning "we do not know which"
    into three confident wrong answers.
    """
    original = uuid.uuid7()
    rig.commit(rig.asserted("location", original))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="split", from_entity=original, to_entity=uuid.uuid7())
            for _ in range(3)
        ],
        rig.index,
        rig.predicates,
    )

    assert len(plans) == 1
    assert len(plans[0].candidates) == 3


def test_an_unordered_predicate_is_re_sorted_after_substitution(rig: Rig):
    """
    A substitution can move an entity in the sort order.

    Substituting an entity can move it in the order, and an unordered predicate's id is
    the sorted spelling. Re-minting without re-sorting would produce an id that
    minting the same proposition afresh would never produce.
    """
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("sibling", a, b))
    # A survivor that sorts before both, so the substituted entity must move.
    kept = uuid.UUID(int=0)

    plans = plan_rekeys(
        [RekeyRow(seq=9, kind="merge", from_entity=b, to_entity=kept)],
        rig.index,
        rig.predicates,
    )

    assert plans[0].sole() == rig.predicates.belief_id("sibling", kept, a)


def test_merging_the_two_entities_of_one_belief_keeps_its_arity(rig: Rig):
    """
    ``sibling(a, b)`` with ``b`` absorbed into ``a`` becomes ``sibling(a, a)``.

    Degenerate as a proposition, and deliberately not collapsed: dropping the repeat
    would re-mint at arity 1, which the predicate does not declare and which no
    subsequent mint of the same claim would ever produce.
    """
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("sibling", a, b))

    plans = plan_rekeys(
        [RekeyRow(seq=9, kind="merge", from_entity=b, to_entity=a)],
        rig.index,
        rig.predicates,
    )

    assert plans[0].sole() == rig.predicates.belief_id("sibling", a, a)


def test_a_belief_this_registry_cannot_re_mint_is_reported_not_skipped(rig: Rig):
    """
    A belief the caller cannot re-key is a real gap in the cascade.

    Silence about it is exactly the failure the seam exists to prevent, so the default
    raises and dropping it takes an argument that says so.
    """
    entity = uuid.uuid7()
    _index_foreign(rig, "location", entity)
    strangers = build_predicates("crm")
    # A registry that does not declare `location` at all cannot render the id.
    rows = [RekeyRow(seq=9, kind="merge", from_entity=entity, to_entity=uuid.uuid7())]

    narrow = _registry_without_location()
    with pytest.raises(UnrenderableBelief, match="cannot be re-minted"):
        plan_rekeys(rows, rig.index, narrow)

    assert plan_rekeys(rows, rig.index, narrow, skip_unrenderable=True) == []
    # ...and a registry that *does* declare it re-mints happily, foreign namespace and
    # all, because the namespace travels with the id rather than being assumed.
    assert plan_rekeys(rows, rig.index, strangers)[0].sole().startswith("v1:other:location(")


def _index_foreign(rig: Rig, predicate: str, *entity_ids: uuid.UUID) -> None:
    """An index row another application's registry minted, sharing this index."""
    other = build_predicates("other")
    minted = other.mint(predicate, *entity_ids)
    with rig.store.transaction():
        rig.index_store.minted(BeliefRow(other.render(minted), predicate, minted.entity_ids, 1))


def _registry_without_location():
    from anagrapheus.beliefs import Predicate, PredicateKind, PredicateRegistry

    return PredicateRegistry([Predicate("knows", 2, PredicateKind.RELATIONAL)], namespace="crm")


def test_a_chain_of_merges_in_one_batch_composes(rig: Rig):
    """
    A is absorbed into B, then B into C, and the caller asks once.

    Planning each row against the index alone stops at B — the belief was never minted
    on B, so nothing plans it onward — and the caller writes a belief keyed on an id
    that the same batch already superseded. The cascade has to see the batch whole.
    """
    a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=b),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=c),
        ],
        rig.index,
        rig.predicates,
    )

    assert [p.sole() for p in plans] == [rig.predicates.belief_id("location", c)]


def test_a_split_product_later_merged_composes_too(rig: Rig):
    """The chain need not be all merges: a product can be absorbed in the same batch."""
    original, uk, us, group = uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("employer", original))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="split", from_entity=original, to_entity=uk),
            RekeyRow(seq=9, kind="split", from_entity=original, to_entity=us),
            RekeyRow(seq=10, kind="merge", from_entity=us, to_entity=group),
        ],
        rig.index,
        rig.predicates,
    )

    assert len(plans) == 1
    assert plans[0].candidates == (
        rig.predicates.belief_id("employer", uk),
        rig.predicates.belief_id("employer", group),
    )


def test_a_merge_cycle_in_a_batch_does_not_hang(rig: Rig):
    """
    A cycle is a contradiction in the log, and identity detects it at read.

    The planner must not loop following one, and must not silently produce a target
    either: it says it cannot re-key rather than picking an arbitrary point in a ring.
    """
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a))

    with pytest.raises(BeliefError):
        plan_rekeys(
            [
                RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=b),
                RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=a),
            ],
            rig.index,
            rig.predicates,
        )


def test_an_unrenderable_belief_names_its_actual_cause(rig: Rig):
    """
    ``UnknownScheme`` for every cause is a lie a consumer cannot see past.

    A migration problem, corrupt data and a mis-configured registry need different
    responses, so the error is its own type and keeps the original as its cause.
    """
    entity = uuid.uuid7()
    _index_foreign(rig, "location", entity)
    rows = [RekeyRow(seq=9, kind="merge", from_entity=entity, to_entity=uuid.uuid7())]

    with pytest.raises(UnrenderableBelief) as caught:
        plan_rekeys(rows, rig.index, _registry_without_location())

    assert isinstance(caught.value.__cause__, UnknownPredicate)
