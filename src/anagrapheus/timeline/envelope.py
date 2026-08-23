"""
Event envelope and payload base types.

The envelope is freeze-content: the field set here is the wire format the log carries
for years. ``seq`` is deliberately absent from the envelope — the store assigns it at
append (``append(batch) -> last_seq``), so an event awaiting append and an event read
back are distinct types.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

__all__ = ["DomainEvent", "Envelope", "PendingEvent", "StoredEvent"]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Envelope(BaseModel):
    """
    Metadata for one event; versioned from birth.

    The registry key is ``(namespace, type, schema_version)``. The ``namespace`` field
    exists so that library-emitted events can never collide with the application's
    vocabulary; ``"anagrapheus"`` is reserved for this library's own events.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid.uuid7)
    namespace: str
    type: str
    schema_version: int = Field(ge=1)
    causation_id: UUID | None = None
    correlation_id: UUID | None = None
    created_at: AwareDatetime = Field(default_factory=_utcnow)


class DomainEvent(BaseModel):
    """
    Base class for event payloads.

    Carries no metadata: naming and versioning belong to the registry, so applications
    subclass this into their own vocabulary and register the concrete types at the
    composition root.
    """

    model_config = ConfigDict(frozen=True)


class PendingEvent(BaseModel):
    """An event awaiting append: envelope plus serialized payload, no ``seq`` yet."""

    model_config = ConfigDict(frozen=True)

    envelope: Envelope
    payload: dict[str, JsonValue]


class StoredEvent(BaseModel):
    """
    An event as read back from the store.

    ``payload`` is raw (pre-upcast); ``EventReader`` upcasts and decodes it into the
    registered model.
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    envelope: Envelope
    payload: dict[str, JsonValue]
