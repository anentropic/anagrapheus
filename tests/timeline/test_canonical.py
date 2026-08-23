"""The canonical serialization must be stable: it keys every pin."""

import unicodedata

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
