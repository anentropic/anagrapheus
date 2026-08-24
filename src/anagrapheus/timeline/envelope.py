"""
Event envelope and payload base types.

The envelope is freeze-content: the field set here is the wire format the log
carries for years, so payload evolution happens through ``schema_version`` plus
upcast-on-read, never by changing these fields. ``seq`` is deliberately absent
from the envelope — the store assigns it at append (``append(batch) -> last_seq``),
so an event awaiting append and an event read back are distinct types.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, JsonValue

__all__ = ["DomainEvent", "Envelope", "PendingEvent", "StoredEvent"]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Envelope(BaseModel, frozen=True):
    """
    Metadata for one event.

    The registry key is ``(namespace, type, schema_version)``. ``namespace`` exists
    so library-emitted events can never collide with the application's vocabulary
    (``"anagrapheus"`` is reserved for this library's own). ``schema_version`` names
    the payload shape this event was written with: stored events are never rewritten,
    so readers bring old versions forward with upcast-on-read (see
    :mod:`anagrapheus.timeline.upcast`).

    ``causation_id`` points at the event that directly caused this one;
    ``correlation_id`` groups every event of one wider activity (a run, a unit of
    work), so a whole causal story is one indexed filter away.
    """

    event_id: UUID = Field(default_factory=uuid.uuid7)
    namespace: str
    type: str
    schema_version: int = Field(ge=1)
    causation_id: UUID | None = None
    correlation_id: UUID | None = None
    created_at: AwareDatetime = Field(default_factory=_utcnow)


class DomainEvent(BaseModel, frozen=True):
    """
    Base class for event payloads.

    Carries no metadata: naming and versioning belong to the registry, so applications
    subclass this into their own vocabulary and register the concrete types at the
    composition root.
    """


class PendingEvent(BaseModel, frozen=True):
    """An event awaiting append: envelope plus serialized payload, no ``seq`` yet."""

    envelope: Envelope
    payload: dict[str, JsonValue]


class StoredEvent(BaseModel, frozen=True):
    """
    An event as read back from the store.

    ``payload`` is raw (pre-upcast); ``EventReader`` upcasts and decodes it into the
    registered model.
    """

    seq: int = Field(ge=1)
    envelope: Envelope
    payload: dict[str, JsonValue]
