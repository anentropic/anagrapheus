"""The index adapter: what it stores, what it answers, and what it costs to ask."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from anagrapheus.beliefs import BeliefMinted, mint
from anagrapheus.beliefs.migrations import COMPONENT, LATEST_VERSION
from anagrapheus.timeline.migrations import SchemaTooNew, component_version

from .conftest import Rig, build_rig

if TYPE_CHECKING:
    from pathlib import Path


def test_the_index_answers_the_question_neither_library_can(rig: Rig):
    """Doxastica filters *by* a belief id; identity knows entities. Only this joins them."""
    alice, bob = uuid.uuid7(), uuid.uuid7()
    rig.commit(
        mint(rig.types, rig.predicates, "location", alice)[1],
        mint(rig.types, rig.predicates, "knows", alice, bob)[1],
        mint(rig.types, rig.predicates, "location", bob)[1],
    )

    assert [r.predicate for r in rig.index.beliefs_for(alice)] == ["location", "knows"]
    assert [r.predicate for r in rig.index.beliefs_for(bob)] == ["knows", "location"]
    assert rig.index.beliefs_for(uuid.uuid7()) == []


def test_argument_order_survives_the_round_trip(rig: Rig):
    """
    The argument slot is kept, not just the set of entities.

    ``reports_to(a, b)`` is not ``reports_to(b, a)``, so the index keeps the slot. A
    set of entities could not tell the two apart, and a re-key would substitute blind.
    """
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(mint(rig.types, rig.predicates, "knows", a, b)[1])

    belief_id = rig.predicates.belief_id("knows", a, b)
    assert rig.index.entities_of(belief_id) == (a, b)


def test_a_belief_naming_one_entity_twice_appears_once(rig: Rig):
    a = uuid.uuid7()
    rig.commit(mint(rig.types, rig.predicates, "sibling", a, a)[1])

    assert len(rig.index.beliefs_for(a)) == 1


def test_an_unknown_belief_id_is_an_ordinary_answer(rig: Rig):
    """
    An id the index has not seen is an ordinary answer, not an error.

    The index is a fold over a range of the log: an id it has not seen may be above the
    position, or another application's entirely.
    """
    assert rig.index.entities_of("v1:crm:location(" + str(uuid.uuid7()) + ")") == ()


def test_beliefs_of_a_predicate(rig: Rig):
    a, b = uuid.uuid7(), uuid.uuid7()
    rig.commit(
        mint(rig.types, rig.predicates, "location", a)[1],
        mint(rig.types, rig.predicates, "employer", a)[1],
        mint(rig.types, rig.predicates, "location", b)[1],
    )

    assert [r.belief_id for r in rig.index.beliefs_of("location")] == [
        rig.predicates.belief_id("location", a),
        rig.predicates.belief_id("location", b),
    ]


def test_re_minting_at_a_shorter_arity_leaves_no_stale_slot(rig: Rig):
    """
    A shorter re-record leaves no orphaned argument slots.

    An id re-recorded with fewer entities would otherwise keep the extra slots, and
    they would travel into every re-key. Reachable only through an application's own
    vocabulary, which the library cannot arity-check for it.
    """
    a, b = uuid.uuid7(), uuid.uuid7()
    long = BeliefMinted(belief_id="v1:crm:knows(x)", predicate="knows", entity_ids=(a, b))
    short = BeliefMinted(belief_id="v1:crm:knows(x)", predicate="knows", entity_ids=(a,))

    rig.index_store.minted(long, 1)
    rig.index_store.minted(short, 2)

    assert rig.index.entities_of("v1:crm:knows(x)") == (a,)
    assert rig.index.beliefs_for(b) == []


def test_beliefs_for_an_entity_is_a_seek_not_a_scan(rig: Rig):
    """The index exists to be asked this on every merge; a scan would make it a cost."""
    plan = rig.store.connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT b.* FROM belief_ids b
        JOIN belief_entities e ON e.belief_id = b.belief_id
        WHERE e.entity_id = ?
        GROUP BY b.belief_id
        ORDER BY b.seq, b.belief_id
        """,
        (str(uuid.uuid7()),),
    ).fetchall()

    assert any("ix_belief_entities_entity" in row["detail"] for row in plan), [
        row["detail"] for row in plan
    ]


def test_the_component_migrates_independently(tmp_path: Path):
    rig = build_rig(tmp_path / "beliefs.db")
    try:
        assert component_version(rig.store.connection, COMPONENT) == LATEST_VERSION
        assert component_version(rig.store.connection, "identity") == 0
    finally:
        rig.store.close()


def test_a_file_from_the_future_refuses_to_open(tmp_path: Path):
    path = tmp_path / "beliefs.db"
    rig = build_rig(path)
    rig.store.connection.execute(
        "UPDATE schema_versions SET version = ? WHERE component = ?",
        (LATEST_VERSION + 1, COMPONENT),
    )
    rig.store.connection.commit()
    rig.store.close()

    with pytest.raises(SchemaTooNew):
        build_rig(path)
