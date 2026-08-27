"""The application-supplied ordinal vocabulary, and what the layer does without one."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from anagrapheus.identity import UNGRADED, Ladder, alias, establish

from .conftest import NVM_LADDER, build_rig

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import Rig


def test_rank_follows_the_declared_order():
    assert NVM_LADDER.rank("certain") > NVM_LADDER.rank("believed")
    assert NVM_LADDER.rank("believed") > NVM_LADDER.rank("suspected")
    assert NVM_LADDER.rank("suspected") > NVM_LADDER.rank("doubted")


def test_an_undeclared_token_ranks_below_every_rung():
    """An old log may carry a vocabulary this process never heard of."""
    assert NVM_LADDER.rank("apocryphal") == -1
    assert NVM_LADDER.rank(UNGRADED) == -1
    assert NVM_LADDER.rank("apocryphal") < NVM_LADDER.rank("doubted")


def test_rungs_must_be_distinct():
    with pytest.raises(ValueError, match="must be distinct"):
        Ladder(("a", "b", "a"))


def test_the_ungraded_token_is_reserved():
    with pytest.raises(ValueError, match="reserved for an ungraded alias"):
        Ladder(("", "believed"))


def test_a_ladder_ranks_candidates_in_the_query(rig: Rig):
    weak, e1 = establish(rig.types, "weak-anchor", provenance="import")
    strong, e2 = establish(rig.types, "strong-anchor", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, weak, "shared", "doubted"),
        alias(rig.types, strong, "shared", "believed"),
    )

    assert [c.entity_id for c in rig.registry.resolve("shared").candidates] == [strong, weak]


def test_without_a_ladder_candidates_stay_in_seq_order(tmp_path: Path):
    """No ladder, no ranking: the layer will not guess which token is stronger."""
    unranked = build_rig(tmp_path / "unranked.db", ladder=None)
    try:
        weak, e1 = establish(unranked.types, "weak-anchor", provenance="import")
        strong, e2 = establish(unranked.types, "strong-anchor", provenance="import")
        unranked.commit(
            e1,
            e2,
            alias(unranked.types, weak, "shared", "doubted"),
            alias(unranked.types, strong, "shared", "believed"),
        )

        candidates = unranked.registry.resolve("shared").candidates
        assert [c.entity_id for c in candidates] == [weak, strong]
    finally:
        unranked.store.close()


def test_a_different_ladder_reverses_the_ranking(tmp_path: Path):
    """The order is the application's, not the library's — invert it and it inverts."""
    inverted = build_rig(
        tmp_path / "inverted.db", ladder=Ladder(("certain", "believed", "suspected", "doubted"))
    )
    try:
        weak, e1 = establish(inverted.types, "weak-anchor", provenance="import")
        strong, e2 = establish(inverted.types, "strong-anchor", provenance="import")
        inverted.commit(
            e1,
            e2,
            alias(inverted.types, weak, "shared", "doubted"),
            alias(inverted.types, strong, "shared", "believed"),
        )

        assert [c.entity_id for c in inverted.registry.resolve("shared").candidates] == [
            weak,
            strong,
        ]
    finally:
        inverted.store.close()


def test_an_established_form_is_ungraded_by_default(rig: Rig):
    """The layer will not assert that a first surface form is certain."""
    entity_id, event = establish(rig.types, "alice@corp", provenance="signup")
    rig.commit(event)

    assert rig.registry.resolve("alice@corp").candidates[0].stance == UNGRADED
    assert rig.registry.surface_forms(entity_id)[0].stance == UNGRADED


def test_establish_can_grade_its_first_form(rig: Rig):
    entity_id, event = establish(rig.types, "alice@corp", provenance="signup", stance="certain")
    rig.commit(event)

    assert rig.registry.resolve("alice@corp").candidates[0].stance == "certain"
    assert rig.registry.surface_forms(entity_id)[0].stance == "certain"


def test_an_ungraded_candidate_sorts_below_a_graded_one(rig: Rig):
    ungraded, e1 = establish(rig.types, "ungraded-anchor", provenance="import")
    graded, e2 = establish(rig.types, "graded-anchor", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, ungraded, "shared"),
        alias(rig.types, graded, "shared", "doubted"),
    )

    assert [c.entity_id for c in rig.registry.resolve("shared").candidates] == [graded, ungraded]
