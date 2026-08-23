"""The canonical serialization must be stable: it keys every pin."""

import unicodedata

import pytest

from anagrapheus.timeline._canonical import canonical_json, sha256_hex


def test_sorted_keys_no_whitespace():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_insertion_order_irrelevant():
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


def test_nested_structures_normalized():
    assert canonical_json({"x": [{"b": None, "a": True}]}) == '{"x":[{"a":true,"b":null}]}'


def test_nfc_normalization_of_strings_and_keys():
    composed = "é"  # U+00E9
    decomposed = unicodedata.normalize("NFD", composed)  # e + combining accent
    assert composed != decomposed
    assert sha256_hex({composed: composed}) == sha256_hex({decomposed: decomposed})


def test_non_ascii_not_escaped():
    assert canonical_json({"k": "é"}) == '{"k":"é"}'


def test_value_difference_changes_hash():
    assert sha256_hex({"a": 1}) != sha256_hex({"a": 2})


def test_tuples_and_abstract_mappings_normalize():
    import unicodedata as ud
    from types import MappingProxyType

    decomposed = ud.normalize("NFD", "é")
    assert canonical_json({"t": (decomposed, 1)}) == canonical_json({"t": ["é", 1]})
    assert canonical_json(MappingProxyType({"a": 1})) == '{"a":1}'


def test_nfc_key_collision_rejected():
    import unicodedata as ud

    composed = "é"
    decomposed = ud.normalize("NFD", composed)
    with pytest.raises(ValueError, match="duplicate key"):
        canonical_json({composed: 1, decomposed: 2})


def test_nan_and_infinity_rejected():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical_json({"x": float("inf")})
