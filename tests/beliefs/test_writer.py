"""Belief events: minting is not optional, values are checked before append, causes travel."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, Stance

from anagrapheus.beliefs import OutsideVocabulary, WrongArity, plan_rekeys
from anagrapheus.identity.store import RekeyRow

from .conftest import Believer, Rig, build_predicates

if TYPE_CHECKING:
    from doxastica import MemoryCore


def test_a_belief_is_written_under_the_minted_id(believer: Believer, core: MemoryCore):
    """A caller holding the events helper never types a belief id, which is the whole point."""
    alice = uuid.uuid7()

    believer.revise("location", (alice,), "London")

    held = core.query_scope("agent", BeliefFilter())
    assert [s.belief_id for s in held] == [build_predicates().belief_id("location", alice)]
    assert [s.value for s in held] == ["London"]


def test_a_relational_predicate_refuses_a_substantive_value(rig: Rig, believer: Believer):
    """
    The relational trap's other face: a third party hidden in the value.

    The arity rule stops ``knows(alice)`` being minted; this stops ``knows(alice, bob)``
    being written with a *third* party in the value, which would smuggle a proposition
    into the answer and retract it on the next revision. Refused while building the
    event, so nothing reaches the log.
    """
    alice, bob, carol = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    with pytest.raises(TypeError, match="relational"):
        rig.events.revise("agent", "knows", (alice, bob), str(carol))
    assert rig.store.last_seq() == 0

    # A bool says whether the proposition holds, and is accepted.
    believer.revise("knows", (alice, bob), True)


def test_a_stance_is_not_a_value_even_for_a_relational_predicate(rig: Rig):
    """
    A grade is not a value.

    It looks like it belongs and it does not: *how firmly* a belief is held is
    doxastica's own ``stance`` argument, one axis over. Taking it as the value would
    merge ranking with epistemic state — and doxastica's encoding refuses it anyway,
    since values are JSON.
    """
    alice, bob = uuid.uuid7(), uuid.uuid7()

    with pytest.raises(TypeError, match="`stance` argument"):
        rig.events.revise("agent", "knows", (alice, bob), Stance.suspected)


def test_the_stance_argument_survives_the_log(believer: Believer):
    """The stance travels by name, doxastica's own wire form, and comes back as the enum."""
    state = believer.revise("knows", (uuid.uuid7(), uuid.uuid7()), True, stance=Stance.suspected)

    assert state.stance is Stance.suspected


def test_a_functional_predicate_takes_any_json_value(believer: Believer):
    """``location(alice)`` is answered by a place; the value is opaque to us all."""
    state = believer.revise("location", (uuid.uuid7(),), {"city": "London", "since": 2019})

    assert state.value == {"city": "London", "since": 2019}


def test_a_value_the_log_cannot_carry_is_refused_before_append(rig: Rig):
    """The value is in the event now, so a value that is not JSON never becomes one."""
    with pytest.raises(ValueError, match="value"):
        rig.events.revise("agent", "location", (uuid.uuid7(),), object())


def test_the_wrong_arity_is_refused_before_append(rig: Rig):
    with pytest.raises(WrongArity):
        rig.events.revise("agent", "knows", (uuid.uuid7(),), True)
    assert rig.store.last_seq() == 0


def test_a_declared_vocabulary_is_held_on_every_write(rig: Rig, believer: Believer):
    """
    Two spellings of one answer cannot become two answers.

    The refusal happens before an event exists, so a rejected write leaves nothing in
    the log for the fold to choke on — and ``expand`` is held to it as much as
    ``revise``.
    """
    account = uuid.uuid7()

    with pytest.raises(OutsideVocabulary, match="'premium'"):
        rig.events.revise("agent", "tier", (account,), "premium")
    with pytest.raises(OutsideVocabulary):
        rig.events.expand("agent", "tier", (account,), "premium")
    assert rig.store.last_seq() == 0

    assert believer.revise("tier", (account,), "pro").value == "pro"


def test_revising_a_relational_belief_leaves_the_other_party_alone(
    believer: Believer, core: MemoryCore
):
    """
    Keying a relation on one side only, shown to be unrepresentable.

    Keyed as ``knows(alice)``, revising bob to carol would leave one belief and retract
    the claim about bob. Keyed as ``knows(alice, bob)`` and ``knows(alice, carol)``,
    both survive — which is what "the id names the question" buys.
    """
    alice, bob, carol = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    believer.revise("knows", (alice, bob), True)
    believer.revise("knows", (alice, carol), True)

    held = core.query_scope("agent", BeliefFilter())
    assert len(held) == 2
    assert all(s.status == "active" for s in held)


def test_a_scope_is_a_holder_not_a_database(believer: Believer, core: MemoryCore):
    """Two holders believing different things about one entity is ordinary, not a conflict."""
    alice = uuid.uuid7()

    believer.revise("location", (alice,), "London")
    believer.for_scope("rumour").revise("location", (alice,), "Paris")

    assert [s.value for s in core.query_scope("agent", BeliefFilter())] == ["London"]
    assert [s.value for s in core.query_scope("rumour", BeliefFilter())] == ["Paris"]


def test_the_source_is_the_belief_event_and_the_cause_is_its_envelopes(rig: Rig):
    """
    A belief's ``source_event_id`` is the event it was folded from; *why* is the envelope's.

    The source has to be the belief event: it is what lets the fold tell a replay from a
    second write, and what keeps doxastica's as-of order the log's order. "Which event
    caused this belief" is still answerable, and answered the way the rest of the stack
    answers it — by ``causation_id``.
    """
    caused_by = uuid.uuid7()
    event = rig.events.revise(
        "agent", "location", (uuid.uuid7(),), "London", causation_id=caused_by
    )

    rig.commit(event)

    [state] = rig.core.query_scope("agent", BeliefFilter())
    assert state.source_event_id == event.envelope.event_id
    [stored] = rig.store.read()
    assert stored.envelope.causation_id == caused_by


def test_contract_retracts_the_minted_id(believer: Believer, core: MemoryCore):
    alice = uuid.uuid7()
    believer.revise("location", (alice,), "London")

    believer.contract("location", (alice,))

    assert core.query_scope("agent", BeliefFilter()) == []
    assert [
        s.status for s in core.query_scope("agent", BeliefFilter(), include_retracted=True)
    ] == ["retracted"]


def test_a_belief_event_reaches_the_index_with_nothing_else_to_append(rig: Rig):
    """
    The gap that made the old writer a trap, closed by having nothing left to forget.

    `BeliefWriter` wrote the belief and handed back a minting event to append, and a
    caller who dropped it got a belief no merge could find. Now the belief and the
    index row are both folded from the one event, so there is no second half.
    """
    alice, bob = uuid.uuid7(), uuid.uuid7()

    rig.commit(rig.events.revise("agent", "knows", (alice, bob), True))

    assert [r.belief_id for r in rig.index.beliefs_for(alice)] == [
        rig.predicates.belief_id("knows", alice, bob)
    ]
    plans = plan_rekeys(
        [RekeyRow(seq=99, kind="merge", from_entity=bob, to_entity=uuid.uuid7())],
        rig.index,
        rig.predicates,
    )
    assert len(plans) == 1
