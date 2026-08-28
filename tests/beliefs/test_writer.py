"""The writer: minting is not optional, values are checked, and the event id binds."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, Stance

from anagrapheus.beliefs import BeliefWriter, plan_rekeys
from anagrapheus.identity.store import RekeyRow

from .conftest import Rig, build_predicates

if TYPE_CHECKING:
    from doxastica import MemoryCore


def test_a_belief_is_written_under_the_minted_id(writer: BeliefWriter, core: MemoryCore):
    """A caller holding a writer never types a belief id, which is the whole point."""
    alice = uuid.uuid7()

    writer.revise("location", (alice,), "London", uuid.uuid7())

    held = core.query_scope("agent", BeliefFilter())
    assert [s.belief_id for s in held] == [build_predicates().belief_id("location", alice)]
    assert [s.value for s in held] == ["London"]


def test_a_relational_predicate_refuses_a_substantive_value(writer: BeliefWriter):
    """
    The relational trap's other face: a third party hidden in the value.

    The trap's other face. The arity rule stops ``knows(alice)`` being minted; this
    stops ``knows(alice, bob)`` being written with a *third* party in the value, which
    would smuggle a proposition into the answer and retract it on the next revision.
    """
    alice, bob, carol = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    with pytest.raises(TypeError, match="relational"):
        writer.revise("knows", (alice, bob), carol, uuid.uuid7())

    # A bool says whether the proposition holds, and is accepted.
    writer.revise("knows", (alice, bob), True, uuid.uuid7())


def test_a_stance_is_not_a_value_even_for_a_relational_predicate(writer: BeliefWriter):
    """
    A grade is not a value.

    It looks like it belongs and it does not: *how firmly* a belief is held is
    doxastica's own ``stance`` argument, one axis over. Taking it as the value would
    merge ranking with epistemic state — and doxastica's encoding refuses it anyway,
    since values are JSON.
    """
    alice, bob = uuid.uuid7(), uuid.uuid7()

    with pytest.raises(TypeError, match="`stance` argument"):
        writer.revise("knows", (alice, bob), Stance.suspected, uuid.uuid7())

    state, _ = writer.revise("knows", (alice, bob), True, uuid.uuid7(), Stance.suspected)
    assert state.stance is Stance.suspected


def test_a_functional_predicate_takes_any_value(writer: BeliefWriter):
    """``location(alice)`` is answered by a place; the value is opaque to us all."""
    writer.revise("location", (uuid.uuid7(),), {"city": "London", "since": 2019}, uuid.uuid7())


def test_revising_a_relational_belief_leaves_the_other_party_alone(
    writer: BeliefWriter, core: MemoryCore
):
    """
    The failure §6a warns about, shown to be unrepresentable.

    Keyed as ``knows(alice)``, revising bob to carol would leave one belief and retract
    the claim about bob. Keyed as ``knows(alice, bob)`` and ``knows(alice, carol)``,
    both survive — which is what "the id names the question" buys.
    """
    alice, bob, carol = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    writer.revise("knows", (alice, bob), True, uuid.uuid7())
    writer.revise("knows", (alice, carol), True, uuid.uuid7())

    held = core.query_scope("agent", BeliefFilter())
    assert len(held) == 2
    assert all(s.status == "active" for s in held)


def test_a_scope_is_a_holder_not_a_database(writer: BeliefWriter, core: MemoryCore):
    """Two holders believing different things about one entity is ordinary, not a conflict."""
    alice = uuid.uuid7()

    writer.revise("location", (alice,), "London", uuid.uuid7())
    writer.for_scope("rumour").revise("location", (alice,), "Paris", uuid.uuid7())

    assert [s.value for s in core.query_scope("agent", BeliefFilter())] == ["London"]
    assert [s.value for s in core.query_scope("rumour", BeliefFilter())] == ["Paris"]


def test_the_event_id_travels_into_the_belief_state(writer: BeliefWriter):
    """
    The envelope id is the handle doxastica's ``source_event_id`` wants.

    Doxastica's ``source_event_id`` is an opaque caller handle; the envelope id is
    exactly the handle worth supplying. Passing it makes "which event caused this
    belief" answerable and puts doxastica's as-of ordering in step with the log's.
    """
    caused_by = uuid.uuid7()

    state, _ = writer.revise("location", (uuid.uuid7(),), "London", caused_by)

    assert state.source_event_id == caused_by


def test_contract_retracts_the_minted_id(writer: BeliefWriter, core: MemoryCore):
    alice = uuid.uuid7()
    writer.revise("location", (alice,), "London", uuid.uuid7())

    writer.contract("location", (alice,), uuid.uuid7())

    assert core.query_scope("agent", BeliefFilter()) == []
    assert [
        s.status for s in core.query_scope("agent", BeliefFilter(), include_retracted=True)
    ] == ["retracted"]


def test_writing_through_the_writer_alone_reaches_the_index(rig: Rig, core: MemoryCore):
    """
    The gap that made the writer a trap.

    `BeliefWriter` is advertised as the way to write beliefs, and it minted ids without
    recording that it had. A caller who used only the writer got an empty index — so
    `plan_rekeys` found nothing, and a merge silently re-keyed none of their beliefs.
    That is precisely the hole this layer exists to close, opened by its own front door.

    Writing now hands back the event that records the mint, so the caller cannot append
    their batch without it.
    """
    writer = BeliefWriter(core, rig.predicates, rig.types, scope_id="agent")
    alice, bob = uuid.uuid7(), uuid.uuid7()

    _, minted = writer.revise("knows", (alice, bob), True, uuid.uuid7())
    rig.commit(minted)

    assert [r.belief_id for r in rig.index.beliefs_for(alice)] == [
        rig.predicates.belief_id("knows", alice, bob)
    ]
    # ...and the belief the writer wrote is therefore reachable by a merge cascade.
    plans = plan_rekeys(
        [RekeyRow(seq=99, kind="merge", from_entity=bob, to_entity=uuid.uuid7())],
        rig.index,
        rig.predicates,
    )
    assert len(plans) == 1
