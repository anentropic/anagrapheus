"""Divergence: what per-scope beliefs pay for, and the condition it rests on."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from anagrapheus.beliefs import divergence

from .conftest import Believer, build_predicates, build_rig

if TYPE_CHECKING:
    from pathlib import Path

    from doxastica import MemoryCore


def test_two_holders_disagreeing_is_a_query(believer: Believer, core: MemoryCore):
    alice = uuid.uuid7()
    truth = believer.for_scope("world")

    believer.revise("location", (alice,), "London")
    truth.revise("location", (alice,), "Paris")

    assert [(d.held, d.reference) for d in divergence(core, "agent", "world")] == [
        ("London", "Paris")
    ]


def test_agreement_is_not_divergence(believer: Believer, core: MemoryCore):
    alice = uuid.uuid7()
    believer.revise("location", (alice,), "London")
    believer.for_scope("world").revise("location", (alice,), "London")

    assert divergence(core, "agent", "world") == []


def test_something_only_one_holder_has_is_an_absence_not_a_contradiction(
    believer: Believer, core: MemoryCore
):
    """
    An absence is not a contradiction.

    Treating the two alike would report every gap in a holder's knowledge as a
    disagreement, which would make the query useless on any real agent.
    """
    believer.revise("location", (uuid.uuid7(),), "London")

    assert divergence(core, "agent", "world") == []


def test_divergence_needs_both_scopes_to_have_minted_through_one_registry(tmp_path: Path):
    """
    Divergence needs a shared key, shown failing rather than asserted.

    Two holders believing contradictory things about the same entity look like perfect
    agreement if their ids were minted under different namespaces — the query cannot
    see a disagreement it has no shared key for. That is the argument for making the
    registry the only way to mint. Two folds over one store, each with its own registry.
    """
    alice = uuid.uuid7()
    ours = build_rig(tmp_path / "ours.db", predicates=build_predicates("crm"))
    core = ours.core
    theirs = build_rig(tmp_path / "theirs.db", predicates=build_predicates("other"), core=core)
    try:
        ours.believer("agent").revise("location", (alice,), "London")
        theirs.believer("world").revise("location", (alice,), "Paris")

        assert divergence(core, "agent", "world") == []

        # Minted through one registry, the same two claims are the disagreement they are.
        ours.believer("rumour").revise("location", (alice,), "Paris")
        assert [d.held for d in divergence(core, "agent", "rumour")] == ["London"]
    finally:
        ours.store.close()
        theirs.store.close()
