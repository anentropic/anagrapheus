"""The decision payloads: stance ordering, normalization, and the shape validators."""

from __future__ import annotations

import uuid

import pytest

from anagrapheus.identity import (
    Merged,
    Split,
    SplitAssignment,
    normalize_surface_form,
)


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
