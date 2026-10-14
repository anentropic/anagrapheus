"""
A re-key across several scopes — the loop the library leaves to its consumer.

The finding this milestone exists to surface: `plan_rekeys` returns **one plan per
belief id**, and one belief id lives in as many scopes as there are holders. With a
single scope that distinction is invisible.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter
from examples.negotiation import domain
from examples.negotiation.rekeying import apply_rekeys
from examples.negotiation.scopes import held, stated

from anagrapheus.beliefs import BeliefIndex, plan_rekeys
from anagrapheus.identity import merge, split

if TYPE_CHECKING:
    from examples.negotiation.table import Table


def _plans(table: Table):
    assert table.index_store is not None
    return plan_rekeys(
        table.identity.rekeys_since(0), BeliefIndex(table.index_store), table.predicates
    )


def test_one_plan_cascades_into_every_scope_that_held_it(table: Table):
    """
    The shape of the gap, asserted as numbers: one plan, three scopes.

    `plan_rekeys` works from the entity→belief index, which records what was minted and
    is scope-free by construction. So a plan can say "belief id X becomes Y" and cannot
    say where — the application supplies the scopes, because only it knows how many
    minds it modelled.
    """
    fisheries = table.table_clause("fisheries")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    source = uuid.uuid7()
    for scope, position in (
        (held("uk"), "concede"),
        (stated("uk"), "never"),
        (held("eu"), "hold_firm"),
    ):
        table.believe(scope, "position", (absorbed, fisheries), position, source)
    table.projector.catch_up()

    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()
    plans = _plans(table)

    applied = apply_rekeys(table, plans, source_event=uuid.uuid7())

    assert len(plans) == 1, "one belief id, so one plan"
    assert [(a.scope, a.moved) for a in applied] == [
        (held("eu"), 1),
        (held("uk"), 1),
        (stated("uk"), 1),
    ], "and three scopes to carry it into -- in `table.scopes()` order, by party"


def test_the_queries_still_answer_after_the_merge(table: Table):
    """
    The cascade is only correct if divergence survives it.

    A re-key that moved two of three scopes would leave the survivors keyed on
    different ids, and every divergence query would then report perfect agreement —
    the failure looks like success, which is why this assertion is separate.
    """
    fisheries = table.table_clause("fisheries")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    source = uuid.uuid7()
    table.believe(held("uk"), "position", (absorbed, fisheries), "concede", source)

    # The EU's model of the UK is not a scope any more -- higher-order belief is derived,
    # never stored -- so what has to survive the merge is the **access-belief** it is
    # derived from, and that belief names the absorbed entity. Access is ordinary
    # observable content, so these are ordinary first-order beliefs precisely so
    # they ride the normal pipeline, and this is what that buys: they re-key like
    # anything else, with no machinery of their own.
    remark = table.types.pending(
        domain.SideRemarkOverheard(
            speaker="uk", overheard_by="eu", clause_id=fisheries, suggests="hold_firm"
        )
    )
    table.append(remark)
    table.believe(held("eu"), "witnessed", (absorbed, remark.envelope.event_id), True, source)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", kept, fisheries)
    assert table.model_held_by("eu", "uk").get(position) is None, (
        "before the merge the access-belief names HMG, so the gate does not fire for "
        "the UK and the EU models nothing -- which is correct, not a failure"
    )

    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()

    apply_rekeys(table, _plans(table), source_event=uuid.uuid7())
    table.projector.catch_up()

    assert table.model_held_by("eu", "uk").get(position) == "hold_firm", (
        "the access-belief moved with the merge, so the derived model answers again"
    )
    assert [(d.held, d.reference) for d in table.misreading("eu", "uk")] == [
        ("hold_firm", "concede")
    ]


def test_a_split_leaves_the_choice_to_the_application(table: Table):
    """
    `BeliefRekey.sole()` raises on a split, and that is the library being right.

    Which product a belief belongs to depends on what the belief *says*, which neither
    the belief layer nor identity may interpret. Without a chooser the belief is
    reported undecided — never guessed at, and never silently dropped.
    """
    fisheries = table.table_clause("fisheries")
    bloc = table.seat("nordic-bloc")
    source = uuid.uuid7()
    table.believe(held("eu"), "position", (bloc, fisheries), "align", source)
    table.projector.catch_up()

    norway, denmark = uuid.uuid7(), uuid.uuid7()
    table.append(split(table.types, bloc, [(norway, ["Norway"]), (denmark, ["Denmark"])]))
    table.projector.catch_up()
    plans = _plans(table)
    assert plans and len(plans[0].candidates) == 2

    with pytest.raises(ValueError, match="candidate successors"):
        plans[0].sole()

    applied = apply_rekeys(table, plans, source_event=uuid.uuid7())
    assert [(a.moved, a.undecided) for a in applied] == [(0, (plans[0].belief_id,))]


def test_a_chooser_resolves_a_split(table: Table):
    """With the application's judgment supplied, the same plan lands."""
    fisheries = table.table_clause("fisheries")
    bloc = table.seat("nordic-bloc")
    source = uuid.uuid7()
    table.believe(held("eu"), "position", (bloc, fisheries), "align", source)
    table.projector.catch_up()
    norway, denmark = uuid.uuid7(), uuid.uuid7()
    table.append(split(table.types, bloc, [(norway, ["Norway"]), (denmark, ["Denmark"])]))
    table.projector.catch_up()

    applied = apply_rekeys(
        table,
        _plans(table),
        source_event=uuid.uuid7(),
        # The domain's judgment: fisheries positions follow the coastal state.
        choose=lambda plan, _value: plan.candidates[0],
    )

    assert [(a.moved, a.undecided) for a in applied] == [(1, ())]


def test_a_belief_written_around_the_log_is_invisible_to_the_plan_and_the_rebuild(
    table: Table,
):
    """
    The hole that folding beliefs from the log closed, shown from the outside.

    A belief written straight into the store reads correctly until something asks the
    log. A merge then plans nothing for it, because the entity index is folded from the
    log; and a rebuild drops it, because the store is too. Before that change this was the
    *library's* front door: `BeliefWriter.revise` wrote here and left only a minting
    event behind. Now the store is reachable only as a fold, and this is what reaching
    around it costs.
    """
    fisheries = table.table_clause("fisheries")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    belief_id = table.predicates.belief_id("position", absorbed, fisheries)
    # Deliberately the wrong way: straight into the store, with no event.
    table.core.revise(held("uk"), belief_id, "concede", uuid.uuid7())
    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()

    assert _plans(table) == [], "nothing was indexed, so nothing is planned"
    table.rebuild_beliefs()
    assert table.core.get_revision_chain(belief_id) == [], "and nothing comes back"


def test_two_merged_entities_in_one_belief_both_get_substituted(table: Table):
    """
    Two plans for one belief id, and both substitutions must land.

    `plan_rekeys` emits a plan per identity decision, so one proposition naming two
    re-keyed entities yields two plans sharing an old belief id.

    Collapsing them into a dict keyed on that id keeps only the last, leaving the other
    entity reference stale — a belief that looks re-keyed and is half-wrong. Each plan
    is therefore diffed against the original and the substitutions applied together, so
    the order they arrive in cannot matter.
    """
    # Both the party and the clause turn out to be duplicates.
    old_clause = table.table_clause("fisheries")
    new_clause = table.table_clause("Fisheries and Aquaculture")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    table.believe(held("uk"), "position", (absorbed, old_clause), "concede", uuid.uuid7())
    table.projector.catch_up()

    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.append(merge(table.types, kept=new_clause, absorbed=old_clause))
    table.projector.catch_up()
    plans = _plans(table)
    assert len({p.belief_id for p in plans}) == 1, "two plans, one old belief id"

    apply_rekeys(table, plans, source_event=uuid.uuid7())

    from doxastica import BeliefFilter

    surviving = {s.belief_id for s in table.core.query_scope(held("uk"), BeliefFilter())}
    assert table.predicates.belief_id("position", kept, new_clause) in surviving, (
        "both substitutions landed, not just the last plan's"
    )


def test_a_rekeyed_belief_can_itself_be_rekeyed_again(table: Table):
    """
    The successor must reach the entity index, or the *next* merge plans nothing.

    Writing the survivor straight to `MemoryCore` holds the belief but leaves no event,
    so the index still knows only the old id. Everything reads correctly
    until a second identity decision, which then finds nothing to plan and leaves the
    belief keyed on an entity that no longer names anything — the same quiet failure
    `Table.believe` exists to prevent, reached through the re-key path instead.
    """
    fisheries = table.table_clause("fisheries")
    first, second, third = table.seats["uk"], table.seat("HMG"), table.seat("UKDel")
    table.believe(held("uk"), "position", (third, fisheries), "concede", uuid.uuid7())
    table.projector.catch_up()

    # third -> second, applied; then second -> first, which can only be planned if the
    # first re-key's successor was recorded.
    table.append(merge(table.types, kept=second, absorbed=third))
    table.projector.catch_up()
    assert apply_rekeys(table, _plans(table), source_event=uuid.uuid7())
    table.projector.catch_up()

    table.append(merge(table.types, kept=first, absorbed=second))
    table.projector.catch_up()
    later = [p for p in _plans(table) if p.from_entity == second]

    assert later, "the successor was indexed, so the second merge can plan for it"
    assert apply_rekeys(table, later, source_event=uuid.uuid7())

    from doxastica import BeliefFilter

    surviving = {s.belief_id for s in table.core.query_scope(held("uk"), BeliefFilter())}
    assert table.predicates.belief_id("position", first, fisheries) in surviving


def test_a_rekey_carries_the_original_stance(table: Table):
    """
    A re-key moves a belief; it must not quietly restate it more confidently.

    `MemoryCore.revise` defaults the stance, so writing the survivor without passing
    the original silently promotes a hedged belief to a certain one — a change in what
    the holder claims, made by machinery that was only supposed to change its key.
    """
    from doxastica import BeliefFilter, Stance

    fisheries = table.table_clause("fisheries")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    table.believe(
        held("uk"),
        "position",
        (absorbed, fisheries),
        "concede",
        uuid.uuid7(),
        stance=Stance.suspected,
    )
    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()

    apply_rekeys(table, _plans(table), source_event=uuid.uuid7())

    moved = next(iter(table.core.query_scope(held("uk"), BeliefFilter())))
    assert moved.stance is Stance.suspected, "the hedge survived the re-key"


def test_a_clause_merge_moves_the_derived_model_too(table: Table):
    """
    A clause merge moves the derived model too, not only the stored beliefs.

    Clauses are identity entities as much as parties are, and the derived read must
    canonicalize both.

    The demo's design notes warned about exactly this -- a merge re-keys stored beliefs but
    cannot reach into an immutable event payload, so a derived model has to resolve
    identity at read time -- and the first implementation applied it to the *party*
    reference only. Review round 2 caught the half. A stored position moves to the
    surviving clause while the remark still names the absorbed one, so the two stop
    intersecting: `misreading` reports perfect agreement and a brief line reads
    `unknown`, both silently and both only after a merge.
    """
    kept = table.table_clause("fisheries")
    absorbed = table.table_clause("Fisheries Annex")  # the same clause, tabled twice
    source = uuid.uuid7()

    table.believe(held("uk"), "position", (table.seats["uk"], absorbed), "concede", source)
    remark = table.types.pending(
        domain.SideRemarkOverheard(
            speaker="uk", overheard_by="eu", clause_id=absorbed, suggests="concede"
        )
    )
    table.append(remark)
    table.believe(
        held("eu"), "witnessed", (table.seats["uk"], remark.envelope.event_id), True, source
    )
    table.projector.catch_up()

    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()
    apply_rekeys(table, _plans(table), source_event=uuid.uuid7())
    table.projector.catch_up()

    survivor = table.predicates.belief_id("position", table.seats["uk"], kept)
    assert any(
        s.belief_id == survivor for s in table.core.query_scope(held("uk"), BeliefFilter())
    ), "sanity: the stored belief was re-keyed to the surviving clause"
    assert table.model_held_by("eu", "uk").get(survivor) == "concede", (
        "and the derived model followed it, because the interpreter canonicalizes the "
        "clause reference as well as the party one"
    )
