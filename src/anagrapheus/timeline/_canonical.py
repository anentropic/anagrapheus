"""
Canonical JSON serialization and content hashing.

Pin keys and event payloads must serialize identically across processes and runs:
sorted keys, no whitespace, NFC-normalized strings, UTF-8. Stability here is
load-bearing — a change to this module re-keys every pin.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import cast

__all__ = ["Json", "canonical_json", "concrete_json", "exact_json", "sha256_hex"]

# JSON-serializable data, typed covariantly: pydantic's JsonValue unions invariant
# dict/list, which rejects concrete returns like dict[str, int] in resolver
# signatures.
# A lint aid, not a validator — json.dumps is the runtime arbiter.
type Json = Mapping[str, Json] | Sequence[Json] | str | int | float | bool | None


def _normalize(value: Json) -> Json:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Json] = {}
        for key, item in value.items():
            nfc_key = unicodedata.normalize("NFC", key)
            if nfc_key in normalized:
                # Two distinct input keys collapsing to one would make the surviving
                # value insertion-order-dependent — a silent pin-key collision.
                raise ValueError(f"duplicate key after NFC normalization: {nfc_key!r}")
            normalized[nfc_key] = _normalize(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_normalize(v) for v in value]
    return value


def concrete_json(value: object) -> Json:
    """
    Concretize into plain dict/list/scalar JSON shapes, content untouched.

    A runtime validator, so the parameter is ``object``: pin results must reach
    ``interpret`` in the same runtime shape on the first resolve, on retry (after a
    storage round-trip), and on replay — so a tuple becomes a list *before* it is
    first interpreted, and an unserializable value fails here, before anything is
    persisted. Unlike the canonical form used for keys, strings are NOT normalized:
    results are data, not identity.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        concretized: dict[str, Json] = {}
        for key, item in cast("Mapping[object, object]", value).items():
            if not isinstance(key, str):
                raise TypeError(f"JSON object keys must be str, got {type(key).__name__}")
            concretized[key] = concrete_json(item)
        return concretized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [concrete_json(item) for item in cast("Sequence[object]", value)]
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def canonical_json(value: Json) -> str:
    """Serialize to canonical form: sorted keys, no whitespace, NFC strings."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def exact_json(value: object) -> str:
    """
    Serialize with sorted keys and no whitespace, strings exactly as given.

    The form for content, where :func:`canonical_json` is the form for keys: event
    hashes and recorded events must keep every byte of a string, so a composed and a
    decomposed character stay different.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Json) -> str:
    """Return the SHA-256 hex digest of the canonical serialization."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
