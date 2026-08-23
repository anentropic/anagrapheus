"""
The event-type registry: the log's vocabulary, declared in code.

Applications own event names; the library owns only the substrate. Concrete payload
types are registered here at the composition root, and duplicate registration fails
at import time — schema lives in code, where drift is a crash rather than a runtime
surprise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple, cast

from anagrapheus.timeline.envelope import DomainEvent, Envelope, PendingEvent
from anagrapheus.timeline.errors import DuplicateEventType, UnknownEventType

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from pydantic import JsonValue

__all__ = ["RESERVED_NAMESPACE", "EventKey", "EventTypeRegistry"]

RESERVED_NAMESPACE: Final = "anagrapheus"


class EventKey(NamedTuple):
    """The registry key: what identifies an event type on the wire."""

    namespace: str
    type: str
    schema_version: int


class EventTypeRegistry:
    """
    Maps event keys to payload models and back.

    The only path between raw payloads and typed events, in both directions: models
    are serialized through :meth:`pending` and decoded through :meth:`decode`.
    """

    def __init__(self) -> None:
        self._models: dict[EventKey, type[DomainEvent]] = {}
        self._keys: dict[type[DomainEvent], EventKey] = {}

    def register(self, key: EventKey, model: type[DomainEvent]) -> None:
        """Register a payload model under a key. Duplicates raise immediately."""
        if key in self._models:
            raise DuplicateEventType(f"event key already registered: {key}")
        if model in self._keys:
            raise DuplicateEventType(
                f"model {model.__name__} already registered as {self._keys[model]}"
            )
        self._models[key] = model
        self._keys[model] = key

    def event[E: DomainEvent](
        self, namespace: str, type: str, version: int = 1
    ) -> Callable[[type[E]], type[E]]:
        """Class-decorator sugar over :meth:`register`."""

        def decorate(model: type[E]) -> type[E]:
            self.register(EventKey(namespace, type, version), model)
            return model

        return decorate

    def key_for(self, model: type[DomainEvent]) -> EventKey:
        """Return the key a model was registered under."""
        try:
            return self._keys[model]
        except KeyError:
            raise UnknownEventType(f"model not registered: {model.__name__}") from None

    def pending(
        self,
        event: DomainEvent,
        *,
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> PendingEvent:
        """Wrap a registered payload in an envelope, ready for ``append``."""
        key = self.key_for(type(event))
        envelope = Envelope(
            namespace=key.namespace,
            type=key.type,
            schema_version=key.schema_version,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        payload = cast("dict[str, JsonValue]", event.model_dump(mode="json"))
        return PendingEvent(envelope=envelope, payload=payload)

    def decode(self, key: EventKey, payload: Mapping[str, JsonValue]) -> DomainEvent:
        """Validate a raw payload into the model registered for ``key``."""
        try:
            model = self._models[key]
        except KeyError:
            raise UnknownEventType(f"no model registered for {key}") from None
        return model.model_validate(dict(payload))

    def latest_version(self, namespace: str, type: str) -> int:
        """Return the highest registered schema version for ``(namespace, type)``."""
        versions = [
            k.schema_version for k in self._models if k.namespace == namespace and k.type == type
        ]
        if not versions:
            raise UnknownEventType(f"no versions registered for ({namespace!r}, {type!r})")
        return max(versions)

