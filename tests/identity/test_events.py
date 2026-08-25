"""The decision payloads: stance ordering, normalization, and the shape validators."""

from __future__ import annotations

import uuid

import pytest

from anagrapheus.identity import (
    Merged,
    Split,
    SplitAssignment,
    Stance,
    normalize_surface_form,
)


def test_stance_is_ordinal():
    assert Stance.CERTAIN > Stance.BELIEVED > Stance.SUSPECTED > Stance.DOUBTED
    assert Stance.DOUBTED < Stance.CERTAIN
    assert Stance.BELIEVED >= Stance.BELIEVED


def test_stance_supports_no_arithmetic():
    """R21: comparison is the only operation. Nothing averages two stances."""
    with pytest.raises(TypeError):
        _ = Stance.CERTAIN + Stance.DOUBTED  # type: ignore[operator]


def test_stance_compares_only_with_stance():
    with pytest.raises(TypeError, match="compares only with stance"):
        _ = Stance.CERTAIN > "believed"  # type: ignore[operator]


def test_stance_serializes_as_its_name():
    event = SplitAssignment(entity_id=uuid.uuid7(), surface_forms=["a"])
    assert event.model_dump(mode="json")["surface_forms"] == ["a"]
    assert Stance.SUSPECTED.value == "suspected"


def test_normalization_is_nfc_only():
    """Composed and decomposed spellings unify; case and spacing are left alone."""
    assert normalize_surface_form("Amélie") == normalize_surface_form("Amélie")
    assert normalize_surface_form("Alice") != normalize_surface_form("alice")
    assert normalize_surface_form(" Alice ") != normalize_surface_form("Alice")


def test_merge_rejects_self_merge():
    entity_id = uuid.uuid7()
    with pytest.raises(ValueError, match="merged into itself"):
        Merged(kept=entity_id, absorbed=entity_id)


def test_split_requires_a_new_entity():
    with pytest.raises(ValueError, match="at least one new entity"):
        Split(original=uuid.uuid7(), into=[])


def test_split_rejects_the_original_as_a_product():
    original = uuid.uuid7()
    with pytest.raises(ValueError, match="differ from the original"):
        Split(
            original=original,
            into=[SplitAssignment(entity_id=original, surface_forms=["x"])],
        )


def test_split_rejects_duplicate_products():
    duplicate = uuid.uuid7()
    with pytest.raises(ValueError, match="must be distinct"):
        Split(
            original=uuid.uuid7(),
            into=[
                SplitAssignment(entity_id=duplicate, surface_forms=["x"]),
                SplitAssignment(entity_id=duplicate, surface_forms=["y"]),
            ],
        )


def test_payloads_are_frozen():
    event = Merged(kept=uuid.uuid7(), absorbed=uuid.uuid7())
    with pytest.raises(ValueError, match="frozen"):
        event.kept = uuid.uuid7()  # type: ignore[misc]
