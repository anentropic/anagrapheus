"""
Decoding an ``events`` row: shared by the SQLite store's reads and its v2 backfill.

The backfill must chain each row exactly as a read will verify it, so both decode
through this one function rather than two copies that could drift apart.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Final, cast
from uuid import UUID

from anagrapheus.timeline.envelope import Envelope

if TYPE_CHECKING:
    from pydantic import JsonValue

__all__ = ["EVENT_COLUMNS", "decode_event_row"]

#: The columns :func:`decode_event_row` takes, in order.
EVENT_COLUMNS: Final = (
    "seq, event_id, namespace, type, schema_version, causation_id, correlation_id,"
    " created_at, payload"
)


def decode_event_row(
    event_id: str,
    namespace: str,
    type_: str,
    schema_version: int,
    causation_id: str | None,
    correlation_id: str | None,
    created_at: str,
    payload: str,
) -> tuple[Envelope, dict[str, JsonValue]]:
    """Return the envelope and raw payload a row stores."""
    envelope = Envelope(
        event_id=UUID(event_id),
        namespace=namespace,
        type=type_,
        schema_version=schema_version,
        causation_id=UUID(causation_id) if causation_id else None,
        correlation_id=UUID(correlation_id) if correlation_id else None,
        created_at=datetime.fromisoformat(created_at),
    )
    return envelope, cast("dict[str, JsonValue]", json.loads(payload))
