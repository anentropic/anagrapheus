"""The re-key seam: what a merge computes, what a split refuses to guess."""

from __future__ import annotations

import itertools
import uuid
from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, Status
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.beliefs import (
    BeliefError,
    BeliefRekey,
    BeliefRow,
    MalformedRekeyBatch,
    UnknownPredicate,
    UnrenderableBelief,
    plan_rekeys,
)
from anagrapheus.identity import (
    IdentityFold,
    IdentityRegistry,
    Merged,
    SQLiteIdentityStore,
    Unmerged,
    establish,
    merge,
    unmerge,
)
from anagrapheus.identity.store import RekeyRow
from anagrapheus.timeline import EventReader, Projector, UpcastRegistry

from ..identity.test_fold import opcodes
from ..identity.test_unmerge_model import GHOST, KINDS, history
from .conftest import Rig, build_predicates, build_rig

if TYPE_CHECKING:
    from collections.abc import Callable


def test_a_merge_computes_one_successor_per_affected_belief(rig: Rig):
    """
    A merge's re-keys are computed outright, not left for the caller to work out.

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

    A merge into an entity that has yielded is dropped, so a ring of two or more merges
    never lands, and ``Merged`` refuses an entity merged into itself. Rows built by hand
    can still loop, and one naming the same entity on both sides is the shortest. The
    planner must not loop following one, and must not silently produce a target either:
    it says it cannot re-key rather than picking an arbitrary point in a ring.
    """
    a = uuid.uuid7()
    rig.commit(rig.asserted("location", a))

    with pytest.raises(BeliefError):
        plan_rekeys(
            [RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=a)],
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


def test_an_unmerge_yields_two_candidates_and_refuses_to_choose(rig: Rig):
    """
    An unmerge is a split's question again.

    A belief keyed on the survivor may have been about the entity it gave back all
    along, and only what the belief says can tell. So the plan names both, the
    survivor's own id included, and ``sole()`` refuses.
    """
    kept, absorbed = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", kept))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="unmerge", from_entity=kept, to_entity=kept),
            RekeyRow(seq=9, kind="unmerge", from_entity=kept, to_entity=absorbed),
        ],
        rig.index,
        rig.predicates,
    )

    assert len(plans) == 1
    assert set(plans[0].candidates) == {
        rig.predicates.belief_id("location", kept),
        rig.predicates.belief_id("location", absorbed),
    }
    with pytest.raises(ValueError, match="2 candidate successors after an unmerge"):
        plans[0].sole()


@pytest.mark.parametrize("after_a_valid_unmerge", [False, True])
def test_an_unmerge_that_gives_nothing_back_is_refused_clearly(
    rig: Rig, after_a_valid_unmerge: bool
):
    """
    Hand-built rows for an unmerge that only name the survivor are malformed.

    Identity never writes them: an unmerge always gives an entity back. Such a
    decision has nowhere to lead, so the planner refuses the batch with
    ``MalformedRekeyBatch``, whether or not another unmerge's walk would reach it,
    rather than planning nothing or leaking a bare ``StopIteration``.
    """
    kept, absorbed = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", kept))
    valid = [
        RekeyRow(seq=9, kind="unmerge", from_entity=kept, to_entity=kept),
        RekeyRow(seq=9, kind="unmerge", from_entity=kept, to_entity=absorbed),
    ]
    malformed = [RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=kept)]

    with pytest.raises(MalformedRekeyBatch, match="gives nothing back"):
        plan_rekeys(
            (valid if after_a_valid_unmerge else []) + malformed,
            rig.index,
            rig.predicates,
        )


def test_a_merge_with_more_than_one_survivor_is_refused_clearly(rig: Rig):
    """
    A merge names exactly one survivor; rows naming two are malformed.

    Planning onto the first would guess at a decision the rows never made, so the
    planner refuses the batch instead.
    """
    absorbed, one, other = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", absorbed))

    with pytest.raises(MalformedRekeyBatch, match="names 2 survivors"):
        plan_rekeys(
            [
                RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=one),
                RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=other),
            ],
            rig.index,
            rig.predicates,
        )


def test_a_merge_row_read_twice_still_plans_once(rig: Rig):
    """
    Joining two overlapping reads can repeat a merge row; it is still one survivor.

    The planner treats the repeat as the same decision, not as a merge naming two.
    """
    absorbed, kept = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", absorbed))
    row = RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=kept)

    assert plan_rekeys([row, row], rig.index, rig.predicates) == plan_rekeys(
        [row], rig.index, rig.predicates
    )


def test_a_merge_withdrawn_later_in_the_batch_plans_nothing(rig: Rig):
    """
    B into A, then that merge withdrawn, in one batch: B is canonical again.

    A belief keyed on B is already keyed correctly. Planning the merge alone would
    move it confidently onto A, which the same batch has taken back.
    """
    kept, absorbed = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", absorbed))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=absorbed),
        ],
        rig.index,
        rig.predicates,
    )

    assert plans == []


def test_a_chain_stops_at_a_merge_withdrawn_later_in_the_batch(rig: Rig):
    """A into B, B into C, then B taken back from C: beliefs on A settle on B."""
    a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a), rig.asserted("location", c))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=b),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=c),
            RekeyRow(seq=11, kind="unmerge", from_entity=c, to_entity=c),
            RekeyRow(seq=11, kind="unmerge", from_entity=c, to_entity=b),
        ],
        rig.index,
        rig.predicates,
    )

    by_belief = {p.belief_id: p for p in plans}
    assert by_belief[rig.predicates.belief_id("location", a)].sole() == (
        rig.predicates.belief_id("location", b)
    )
    assert len(by_belief[rig.predicates.belief_id("location", c)].candidates) == 2


def test_a_merge_redone_after_an_unmerge_is_followed(rig: Rig):
    """Merged, withdrawn, merged again: the second merge is the one that holds."""
    kept, absorbed = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", absorbed))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=absorbed),
            RekeyRow(seq=11, kind="merge", from_entity=absorbed, to_entity=kept),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.sole()) for p in plans] == [(11, rig.predicates.belief_id("location", kept))]


def test_an_unmerge_from_the_identity_layer_plans_as_ambiguous(rig: Rig):
    """The rows identity actually emits, end to end, not hand-built ones."""
    identity_store = SQLiteIdentityStore(rig.store.connection)
    identity = IdentityRegistry(identity_store)
    projector = Projector(
        rig.store,
        EventReader(rig.store, UpcastRegistry(), rig.types),
        [IdentityFold(identity_store, rig.types), rig.fold],
    )
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.store.append([e1, e2, merge(rig.types, kept=kept, absorbed=absorbed)])
    rig.store.append([rig.asserted("location", kept)])
    projector.catch_up()
    before = rig.store.last_seq()

    rig.store.append([unmerge(rig.types, identity, kept=kept, absorbed=absorbed)])
    projector.catch_up()

    plans = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
    assert len(plans) == 1
    assert plans[0].kind == "unmerge"
    assert set(plans[0].candidates) == {
        rig.predicates.belief_id("location", kept),
        rig.predicates.belief_id("location", absorbed),
    }


def test_merged_withdrawn_and_merged_again_leaves_the_survivor_one_successor(rig: Rig):
    """
    A belief on the survivor through merge, unmerge, merge in one batch.

    The id the unmerge gave back is absorbed again, so both of the unmerge's targets
    end on the survivor: one successor, not a ring.
    """
    kept, absorbed = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", kept))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=absorbed, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=kept),
            RekeyRow(seq=10, kind="unmerge", from_entity=kept, to_entity=absorbed),
            RekeyRow(seq=11, kind="merge", from_entity=absorbed, to_entity=kept),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.sole()) for p in plans] == [(10, rig.predicates.belief_id("location", kept))]


def test_an_older_merge_unmerged_after_the_survivor_moves_in_the_batch_offers_both(rig: Rig):
    """
    B was merged into A before the batch; the batch takes A into Z, then B out of A.

    Identity's unmerge rows come from Z, where A stands by then. What was B's still
    sits on A in the index, since the batch has not moved it yet, so the beliefs on A
    are offered B as well as Z, and so are those on Z.
    """
    a, b, z = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a), rig.asserted("location", z))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=z),
            RekeyRow(seq=10, kind="unmerge", from_entity=z, to_entity=z),
            RekeyRow(seq=10, kind="unmerge", from_entity=z, to_entity=b),
        ],
        rig.index,
        rig.predicates,
    )

    on_z, on_b = rig.predicates.belief_id("location", z), rig.predicates.belief_id("location", b)
    assert [(p.seq, p.from_entity, p.candidates) for p in plans] == [
        (9, a, (on_z,)),
        (10, z, (on_z, on_b)),
        (10, a, (on_z, on_b)),
    ]


def test_unmerges_that_nest_in_one_batch_offer_every_entity_given_back(rig: Rig):
    """
    A into B and B into C before the batch; the batch gives back B, then A from B.

    Everything sits on C in the index. The second unmerge's rows name B, which holds
    nothing yet, so a belief on C that was A's is reached only through the first
    unmerge's candidate B: it is offered C, B and A.
    """
    a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", c))

    plans = plan_rekeys(
        [
            RekeyRow(seq=10, kind="unmerge", from_entity=c, to_entity=c),
            RekeyRow(seq=10, kind="unmerge", from_entity=c, to_entity=b),
            RekeyRow(seq=11, kind="unmerge", from_entity=b, to_entity=b),
            RekeyRow(seq=11, kind="unmerge", from_entity=b, to_entity=a),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.from_entity, p.candidates) for p in plans] == [
        (10, c, tuple(rig.predicates.belief_id("location", e) for e in (c, b, a)))
    ]


def test_given_back_and_merged_again_many_times_in_one_batch_plans_quickly(rig: Rig):
    """
    Thirty entities in C before the batch; the batch gives each back, then merges it again.

    Each unmerge's two sides meet again on C, where the next unmerge splits them once
    more. Walked afresh at every meeting, that is twice the walking per unmerge, which
    at this depth would not finish; walked once per plan, it is quick. Every belief on
    C is offered C alone, since each id given back is absorbed again.
    """
    identity, projector = _wired(rig)
    c, ec = establish(rig.types, "c", provenance="x")
    given = [establish(rig.types, f"e{i}", provenance="x") for i in range(30)]
    rig.store.append([ec, *(event for _, event in given), rig.asserted("location", c)])
    rig.store.append([merge(rig.types, kept=c, absorbed=entity) for entity, _ in given])
    projector.catch_up()
    before = rig.store.last_seq()

    rig.store.append(
        [
            event
            for entity, _ in given
            for event in (
                rig.types.pending(Unmerged(kept=c, absorbed=entity)),
                merge(rig.types, kept=c, absorbed=entity),
            )
        ]
    )
    projector.catch_up()

    plans = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
    assert [(p.kind, p.from_entity, p.sole()) for p in plans] == [
        ("unmerge", c, rig.predicates.belief_id("location", c))
    ] * len(given)


def test_a_second_merge_of_an_absorbed_entity_plans_nothing(rig: Rig):
    """
    B into A, then B into C with no unmerge between: the first merge stands.

    The identity layer ignores the second, so planning it would move beliefs on B onto
    an entity B never yielded to.
    """
    a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", b))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=b, to_entity=a),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=c),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.sole()) for p in plans] == [(9, rig.predicates.belief_id("location", a))]


def test_a_chain_through_an_entity_merged_twice_follows_the_first_merge(rig: Rig):
    """X into B, B into A, B into C: beliefs on X settle on A."""
    x, a, b, c = uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", x))

    plans = plan_rekeys(
        [
            RekeyRow(seq=8, kind="merge", from_entity=x, to_entity=b),
            RekeyRow(seq=9, kind="merge", from_entity=b, to_entity=a),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=c),
        ],
        rig.index,
        rig.predicates,
    )

    assert [p.sole() for p in plans] == [rig.predicates.belief_id("location", a)]


def test_a_merge_after_an_ignored_one_and_an_unmerge_lands(rig: Rig):
    """
    B into A, B into C (ignored), B out of A, B into D: the last merge holds.

    The ignored merge to C was never withdrawn, but it never stood either, so it does
    not stop B yielding to D.
    """
    a, b, c, d = uuid.uuid7(), uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", b))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=b, to_entity=a),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=c),
            RekeyRow(seq=11, kind="unmerge", from_entity=a, to_entity=a),
            RekeyRow(seq=11, kind="unmerge", from_entity=a, to_entity=b),
            RekeyRow(seq=12, kind="merge", from_entity=b, to_entity=d),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.sole()) for p in plans] == [(12, rig.predicates.belief_id("location", d))]


def test_the_planner_and_the_identity_layer_agree_on_a_double_merge(rig: Rig):
    """The rows identity emits for B into A then B into C, end to end, in one batch."""
    identity_store = SQLiteIdentityStore(rig.store.connection)
    identity = IdentityRegistry(identity_store)
    projector = Projector(
        rig.store,
        EventReader(rig.store, UpcastRegistry(), rig.types),
        [IdentityFold(identity_store, rig.types), rig.fold],
    )
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    c, ec = establish(rig.types, "c", provenance="x")
    rig.store.append([ea, eb, ec, rig.asserted("location", b)])
    projector.catch_up()
    before = rig.store.last_seq()

    # The second merge is built without the registry, so nothing refuses it.
    rig.store.append([merge(rig.types, kept=a, absorbed=b), merge(rig.types, kept=c, absorbed=b)])
    projector.catch_up()

    plans = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
    assert identity.canonical(b) == a
    assert [p.sole() for p in plans] == [
        rig.predicates.belief_id("location", identity.canonical(b))
    ]


def _wired(rig: Rig) -> tuple[IdentityRegistry, Projector]:
    """An identity registry folded from the same log as the beliefs."""
    identity_store = SQLiteIdentityStore(rig.store.connection)
    projector = Projector(
        rig.store,
        EventReader(rig.store, UpcastRegistry(), rig.types),
        [IdentityFold(identity_store, rig.types), rig.fold],
    )
    return IdentityRegistry(identity_store), projector


def test_a_merge_into_an_entity_the_batch_already_absorbed_plans_nothing(rig: Rig):
    """
    A into Z, then B into A: A has yielded, so B cannot yield to it.

    The identity layer ignores the second merge, so planning it would move beliefs on
    B onto A, which identity resolves to Z, while B itself stays canonical.
    """
    a, b, z = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a), rig.asserted("location", b))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=z),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=a),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.seq, p.from_entity, p.sole()) for p in plans] == [
        (9, a, rig.predicates.belief_id("location", z))
    ]


def test_a_reverse_merge_in_a_batch_plans_nothing(rig: Rig):
    """A into B, then B into A: A has yielded, so the second merge closes no ring."""
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(rig.asserted("location", a), rig.asserted("location", b))

    plans = plan_rekeys(
        [
            RekeyRow(seq=9, kind="merge", from_entity=a, to_entity=b),
            RekeyRow(seq=10, kind="merge", from_entity=b, to_entity=a),
        ],
        rig.index,
        rig.predicates,
    )

    assert [(p.from_entity, p.sole()) for p in plans] == [
        (a, rig.predicates.belief_id("location", b))
    ]


def test_the_planner_and_the_identity_layer_agree_across_two_batches(rig: Rig):
    """
    A into Z in one batch, B into A in the next, planned one batch at a time.

    The planner only sees the second batch, where A looks free to take B. Identity
    has to agree with it, and both have to agree on where beliefs on B belong.
    """
    identity, projector = _wired(rig)
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.store.append([ez, ea, eb, *(rig.asserted("location", e) for e in (a, b))])
    projector.catch_up()
    first = rig.store.last_seq()
    rig.store.append([merge(rig.types, kept=z, absorbed=a)])
    projector.catch_up()
    second = rig.store.last_seq()

    # Built without the registry, so nothing refuses it.
    rig.store.append([merge(rig.types, kept=a, absorbed=b)])
    projector.catch_up()

    for since, until in ((first, second), (second, rig.store.last_seq())):
        rows = [row for row in identity.rekeys_since(since) if row.seq <= until]
        for plan in plan_rekeys(rows, rig.index, rig.predicates):
            assert plan.sole() == rig.predicates.belief_id(
                "location", identity.canonical(plan.from_entity)
            )
    assert identity.canonical(b) == b
    assert identity.rekeys_since(second) == []


def test_an_unmerge_after_the_survivor_was_merged_on_offers_where_it_stands_now(rig: Rig):
    """
    B into A and A into Z in one batch, then B out of A in the next.

    A's beliefs went to Z with it, so the unmerge's candidates are Z and B. A, which
    no longer holds anything, is not one of them.
    """
    identity, projector = _wired(rig)
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.store.append([ez, ea, eb, *(rig.asserted("location", e) for e in (z, a, b))])
    projector.catch_up()
    rig.store.append([merge(rig.types, kept=a, absorbed=b), merge(rig.types, kept=z, absorbed=a)])
    projector.catch_up()
    before = rig.store.last_seq()

    rig.store.append([unmerge(rig.types, identity, kept=a, absorbed=b)])
    projector.catch_up()

    (plan,) = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
    assert identity.canonical(a) == z
    assert set(plan.candidates) == {
        rig.predicates.belief_id("location", z),
        rig.predicates.belief_id("location", b),
    }


def test_the_planner_and_the_identity_layer_agree_on_a_merge_of_an_unknown_entity(rig: Rig):
    """
    A belief can name an id nothing established; a merge of it re-keys nothing.

    Identity leaves the id canonical, so a plan moving its beliefs onto the survivor
    would disagree with it, and an unmerge of the pair would be ignored.
    """
    identity, projector = _wired(rig)
    a, ea = establish(rig.types, "a", provenance="x")
    ghost = uuid.uuid7()
    rig.store.append([ea, rig.asserted("location", ghost)])
    projector.catch_up()
    before = rig.store.last_seq()

    rig.store.append([merge(rig.types, kept=a, absorbed=ghost)])
    projector.catch_up()

    assert plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates) == []
    assert identity.canonical(ghost) == ghost


@pytest.mark.parametrize("steps", [2, 3])
@pytest.mark.parametrize(
    "order", list(itertools.permutations(("b from a", "c from a", "a from z"))), ids=" / ".join
)
def test_the_planner_and_the_identity_layer_agree_on_unmerges_of_a_chain(
    rig: Rig, order: tuple[str, ...], steps: int
):
    """
    B and C both bring a form into A, A into Z, then some of the merges are withdrawn.

    Wherever a belief keyed on an absorbed entity is planned to go, identity resolves
    that entity there too, and every candidate an unmerge offers names an entity that
    is canonical again.
    """
    identity_store = SQLiteIdentityStore(rig.store.connection)
    identity = IdentityRegistry(identity_store)
    projector = Projector(
        rig.store,
        EventReader(rig.store, UpcastRegistry(), rig.types),
        [IdentityFold(identity_store, rig.types), rig.fold],
    )
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "ACME", provenance="import")
    c, ec = establish(rig.types, "ACME", provenance="ledger")
    rig.store.append([ez, ea, eb, ec, *(rig.asserted("location", e) for e in (a, b, c))])
    projector.catch_up()
    before = rig.store.last_seq()
    pairs = {"b from a": (a, b), "c from a": (a, c), "a from z": (z, a)}

    rig.store.append(
        [
            merge(rig.types, kept=a, absorbed=b),
            merge(rig.types, kept=a, absorbed=c),
            merge(rig.types, kept=z, absorbed=a),
            *(
                rig.types.pending(Unmerged(kept=pairs[step][0], absorbed=pairs[step][1]))
                for step in order[:steps]
            ),
        ]
    )
    projector.catch_up()

    plans = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
    for plan in plans:
        if plan.kind == "merge":
            assert plan.sole() == rig.predicates.belief_id(
                "location", identity.canonical(plan.from_entity)
            )
        else:
            assert plan.kind == "unmerge"
            assert set(plan.candidates) <= {
                rig.predicates.belief_id("location", e)
                for e in (z, a, b, c)
                if identity.canonical(e) == e
            }
    planned = {plan.from_entity for plan in plans if plan.kind == "merge"}
    assert planned == {e for e in (a, b, c) if identity.canonical(e) != e}


@settings(deadline=None)
@given(ops=opcodes(min_size=10, max_size=30, kinds=KINDS), cut=st.integers(0, 30))
def test_the_planner_and_the_identity_layer_agree_over_generated_histories(
    tmp_path_factory: pytest.TempPathFactory, ops: list[tuple[int, int, int]], cut: int
):
    """
    Generated merges and unmerges over entities that each hold a belief, in two batches.

    Each batch is planned on its own rows and the plan applied before the next batch,
    the way an application does: a revise under the id it picks and a contract of the
    old one. A merge's plan is applied with ``sole()``. Where a plan offers a choice,
    the test picks the candidate it can justify, because it knows which entity each
    belief was first asserted about: the one that entity resolves to now.

    After each batch, every belief held is keyed on a canonical entity, and is the one
    for an entity it was first asserted about. A merge's single successor is where
    identity resolves the entity it was keyed on, and an unmerge offers where that
    entity stands and the entity the unmerge gave back. Every entity planned was left
    merged by its batch.

    The merges may name an id nothing established, which holds a belief all the same,
    and may give an entity to a survivor that has itself been merged, in this batch or
    the one before.
    """
    rig = build_rig(tmp_path_factory.mktemp("rekey-model") / "beliefs.db")
    try:
        identity, projector = _wired(rig)
        decisions = history(ops)
        # Every entity but the ghost is established and holds a belief before the
        # batches, the split products included: the splits themselves are not part of
        # them. The ghost holds a belief too.
        named = {d.subject for d in decisions} | {d.other for d in decisions if d.other}
        entities = sorted(named | {GHOST})
        rig.store.append(
            [
                *(
                    establish(rig.types, "e", provenance="x", entity_id=entity)[1]
                    for entity in entities
                    if entity != GHOST
                ),
                *(rig.asserted("location", entity) for entity in entities),
            ]
        )
        projector.catch_up()
        decided = [
            rig.types.pending(
                (Merged if d.kind == "merge" else Unmerged)(kept=d.other, absorbed=d.subject)
            )
            for d in decisions
            if d.kind in {"merge", "unmerge"} and d.other is not None
        ]
        if not decided:
            return

        def location(entity: uuid.UUID) -> str:
            return rig.predicates.belief_id("location", entity)

        # Which entities each belief held was first asserted about.
        about: dict[str, set[uuid.UUID]] = {location(e): {e} for e in entities}
        at = cut % (len(decided) + 1)
        for batch in (decided[:at], decided[at:]):
            if not batch:
                continue
            before = rig.store.last_seq()
            rig.store.append(batch)
            projector.catch_up()

            rows = identity.rekeys_since(before)
            plans = plan_rekeys(rows, rig.index, rig.predicates)
            canonical = {e for e in entities if identity.canonical(e) == e}
            gave_back = {
                r.seq: r.to_entity
                for r in rows
                if r.kind == "unmerge" and r.from_entity != r.to_entity
            }
            for plan in plans:
                assert set(plan.candidates) <= {location(e) for e in canonical}
                if plan.kind == "merge":
                    assert plan.sole() == location(identity.canonical(plan.from_entity))
                else:
                    assert plan.kind == "unmerge"
                    assert location(identity.canonical(plan.from_entity)) in plan.candidates
                    assert location(identity.canonical(gave_back[plan.seq])) in plan.candidates
            merged = {plan.from_entity for plan in plans if plan.kind == "merge"}
            assert merged.isdisjoint(canonical)

            about = _apply(rig, plans, about, location, identity.canonical)
            assert _held(rig) == set(about)
            for belief_id, origins in about.items():
                assert {location(identity.canonical(e)) for e in origins} == {belief_id}
    finally:
        rig.store.close()


def _held(rig: Rig) -> set[str]:
    """The belief ids the scope holds now."""
    tails = rig.core.query_scope("agent", BeliefFilter(status=frozenset({Status.active})))
    return {tail.belief_id for tail in tails}


def _apply(
    rig: Rig,
    plans: list[BeliefRekey],
    about: dict[str, set[uuid.UUID]],
    location: Callable[[uuid.UUID], str],
    canonical: Callable[[uuid.UUID], uuid.UUID],
) -> dict[str, set[uuid.UUID]]:
    """
    Apply a batch's plans as an application would, and say what each belief is about.

    A belief no longer held is passed over, as an application would find nothing to
    move. A belief whose plans all have one candidate goes there; one that any plan
    offers a choice for goes, for each entity it was about, to the candidate that
    entity resolves to, which has to be among those offered.
    """
    planned: dict[str, list[BeliefRekey]] = {}
    for plan in plans:
        if plan.belief_id in about:
            planned.setdefault(plan.belief_id, []).append(plan)
    after: dict[str, set[uuid.UUID]] = {}
    for belief_id, origins in about.items():
        its = planned.get(belief_id)
        choices = [plan for plan in its or () if len(plan.candidates) > 1]
        for origin in origins:
            if its is None:
                target = belief_id
            elif choices:
                target = location(canonical(origin))
                assert any(target in plan.candidates for plan in choices), (belief_id, origin)
            else:
                (target,) = {plan.sole() for plan in its}
            after.setdefault(target, set()).add(origin)

    def keyed(belief_id: str) -> tuple[uuid.UUID, ...]:
        return rig.predicates.parse(belief_id).entity_ids

    events = [
        *(rig.events.contract("agent", "location", keyed(b)) for b in about.keys() - after.keys()),
        *(rig.asserted("location", *keyed(b)) for b in after.keys() - about.keys()),
    ]
    if events:
        rig.commit(*events)
    return after
