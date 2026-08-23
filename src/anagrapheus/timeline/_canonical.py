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

from pydantic import JsonValue

__all__ = ["canonical_json", "sha256_hex"]


def _normalize(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def canonical_json(value: JsonValue) -> str:
    """Serialize to canonical form: sorted keys, no whitespace, NFC strings."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_hex(value: JsonValue) -> str:
    """Return the SHA-256 hex digest of the canonical serialization."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
