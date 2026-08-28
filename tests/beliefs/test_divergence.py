"""Divergence: doc 00 §6c's payoff, and the condition it rests on."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from anagrapheus.beliefs import BeliefWriter, divergence, register_builtin_events
from anagrapheus.timeline import EventTypeRegistry

from .conftest import build_predicates

if TYPE_CHECKING:
    from doxastica import MemoryCore


def test_two_holders_disagreeing_is_a_query(writer: BeliefWriter, core: MemoryCore):
    alice = uuid.uuid7()
    truth = writer.for_scope("world")

    writer.revise("location", (alice,), "London", uuid.uuid7())
    truth.revise("location", (alice,), "Paris", uuid.uuid7())

    assert [(d.held, d.reference) for d in divergence(core, "agent", "world")] == [
        ("London", "Paris")
    ]


def test_agreement_is_not_divergence(writer: BeliefWriter, core: MemoryCore):
    alice = uuid.uuid7()
    writer.revise("location", (alice,), "London", uuid.uuid7())
    writer.for_scope("world").revise("location", (alice,), "London", uuid.uuid7())

    assert divergence(core, "agent", "world") == []


def test_something_only_one_holder_has_is_an_absence_not_a_contradiction(
    writer: BeliefWriter, core: MemoryCore
):
    """
    An absence is not a contradiction.

    Treating the two alike would report every gap in a holder's knowledge as a
    disagreement, which would make the query useless on any real agent.
    """
    writer.revise("location", (uuid.uuid7(),), "London", uuid.uuid7())

    assert divergence(core, "agent", "world") == []


def test_divergence_needs_both_scopes_to_have_minted_through_one_registry(
    core: MemoryCore,
):
    """
    §6c's condition, shown failing rather than asserted.

    Two holders believing contradictory things about the same entity look like perfect
    agreement if their ids were minted under different namespaces — the query cannot
    see a disagreement it has no shared key for. That is the argument for making the
    registry the only way to mint.
    """
    alice = uuid.uuid7()
    types = EventTypeRegistry()
    register_builtin_events(types)
    ours = BeliefWriter(core, build_predicates("crm"), types, scope_id="agent")
    theirs = BeliefWriter(core, build_predicates("other"), types, scope_id="world")

    ours.revise("location", (alice,), "London", uuid.uuid7())
    theirs.revise("location", (alice,), "Paris", uuid.uuid7())

    assert divergence(core, "agent", "world") == []

    # Minted through one registry, the same two claims are the disagreement they are.
    same = BeliefWriter(core, build_predicates("crm"), types, scope_id="rumour")
    same.revise("location", (alice,), "Paris", uuid.uuid7())
    assert [d.held for d in divergence(core, "agent", "rumour")] == ["London"]
