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

from anagrapheus.beliefs import BeliefIndex, plan_rekeys
from anagrapheus.identity import merge, split
from examples.negotiation.rekeying import apply_rekeys
from examples.negotiation.scopes import held, model_of, stated

if TYPE_CHECKING:
    from examples.negotiation.table import Table


def _plans(table: Table):
    assert table.index_store is not None
    return plan_rekeys(table.identity.rekeys_since(0), BeliefIndex(table.index_store), table.predicates)


def test_one_plan_cascades_into_every_scope_that_held_it(table: Table):
    """
    The shape of the gap, asserted as numbers: **one plan, three scopes.**

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
        (model_of("eu", "uk"), "hold_firm"),
    ):
        table.believe(scope, "position", (absorbed, fisheries), position, source)
    table.projector.catch_up()

    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()
    plans = _plans(table)

    applied = apply_rekeys(table, plans, source_event=uuid.uuid7())

    assert len(plans) == 1, "one belief id, so one plan"
    assert [(a.scope, a.moved) for a in applied] == [
        (held("uk"), 1),
        (stated("uk"), 1),
        (model_of("eu", "uk"), 1),
    ], "and three scopes to carry it into"


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
    for scope, position in (
        (held("uk"), "concede"),
        (model_of("eu", "uk"), "hold_firm"),
    ):
        table.believe(scope, "position", (absorbed, fisheries), position, source)
    table.projector.catch_up()
    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()

    apply_rekeys(table, _plans(table), source_event=uuid.uuid7())

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
    table.append(
        split(table.types, bloc, [(norway, ["Norway"]), (denmark, ["Denmark"])])
    )
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


def test_a_belief_minted_without_appending_is_invisible_to_the_plan(table: Table):
    """
    The quiet failure `Table.believe` exists to prevent.

    `BeliefWriter.revise` hands back the minting event and leaves appending it to the
    caller. Drop it and everything reads correctly — until a merge, which then appears
    to succeed while leaving the belief keyed on an entity that names nothing.
    """
    fisheries = table.table_clause("fisheries")
    kept, absorbed = table.seats["uk"], table.seat("HMG")
    # Deliberately the wrong way: revise, discard the minting event.
    table.writer(held("uk")).revise("position", (absorbed, fisheries), "concede", uuid.uuid7())
    table.projector.catch_up()
    table.append(merge(table.types, kept=kept, absorbed=absorbed))
    table.projector.catch_up()

    assert _plans(table) == [], "nothing was indexed, so nothing is planned"
