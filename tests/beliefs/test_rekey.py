"""The re-key seam: what a merge computes, what a split refuses to guess."""

from __future__ import annotations

import itertools
import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.beliefs import (
    BeliefError,
    BeliefRow,
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

    A merge into an entity that has yielded is dropped, so a ring of two or more merges
    never lands; an entity merged into itself is the ring rows can still carry. The
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


@settings(max_examples=200, deadline=None)
@given(ops=opcodes(min_size=10, max_size=30, kinds=KINDS), cut=st.integers(0, 30))
def test_the_planner_and_the_identity_layer_agree_over_generated_histories(
    tmp_path_factory: pytest.TempPathFactory, ops: list[tuple[int, int, int]], cut: int
):
    """
    Generated merges and unmerges over entities that each hold a belief, in two batches.

    The same agreement as for the chain above, batch by batch, each planned on its own
    rows: a belief keyed on an entity a batch merged is planned onto the entity
    identity resolves it to after that batch, and an unmerge offers only entities that
    are canonical again then. Every entity planned was left merged by its batch, and
    every entity left merged at the end was planned by some batch.

    The merges may name an id nothing established, which holds a belief all the same,
    and may give an entity to a survivor that has itself been merged, in this batch or
    the one before.
    """
    rig = build_rig(tmp_path_factory.mktemp("rekey-model") / "beliefs.db")
    try:
        identity_store = SQLiteIdentityStore(rig.store.connection)
        identity = IdentityRegistry(identity_store)
        projector = Projector(
            rig.store,
            EventReader(rig.store, UpcastRegistry(), rig.types),
            [IdentityFold(identity_store, rig.types), rig.fold],
        )
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
        at = cut % (len(decided) + 1)
        planned: set[uuid.UUID] = set()
        for batch in (decided[:at], decided[at:]):
            if not batch:
                continue
            before = rig.store.last_seq()
            rig.store.append(batch)
            projector.catch_up()

            plans = plan_rekeys(identity.rekeys_since(before), rig.index, rig.predicates)
            canonical = {e for e in entities if identity.canonical(e) == e}
            for plan in plans:
                if plan.kind == "merge":
                    assert plan.sole() == rig.predicates.belief_id(
                        "location", identity.canonical(plan.from_entity)
                    )
                else:
                    assert set(plan.candidates) <= {
                        rig.predicates.belief_id("location", e) for e in canonical
                    }
            merged = {plan.from_entity for plan in plans if plan.kind == "merge"}
            assert merged.isdisjoint(canonical)
            planned |= merged
        assert planned >= {e for e in entities if identity.canonical(e) != e}
    finally:
        rig.store.close()
