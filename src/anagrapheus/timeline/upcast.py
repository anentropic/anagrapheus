"""
Upcast-on-read: schema evolution without rewriting the log.

Upcasts are pure, chainable functions ``(namespace, type, from_version) -> payload``
applied at read. Stored events are never rewritten in place; schema changes are
additive-preferred — optional fields, then new types, then version bumps. A chain
that stops short of the latest registered version raises loudly instead of decoding
a stale shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import JsonValue

from anagrapheus.timeline.errors import DuplicateEventType, UpcastGap
from anagrapheus.timeline.registry import EventKey

if TYPE_CHECKING:
    from collections.abc import Iterator

    from anagrapheus.timeline.envelope import DomainEvent, Envelope, StoredEvent
    from anagrapheus.timeline.registry import EventTypeRegistry
    from anagrapheus.timeline.store import EventStore

__all__ = ["EventReader", "Upcast", "UpcastRegistry"]

type Upcast = Callable[[dict[str, JsonValue]], dict[str, JsonValue]]


class UpcastRegistry:
    """Pure ``(namespace, type, from_version) -> fn`` map, applied at read."""

    def __init__(self) -> None:
        self._upcasts: dict[tuple[str, str, int], Upcast] = {}

    def register(self, namespace: str, type: str, from_version: int, fn: Upcast) -> None:
        """Register the step from ``from_version`` to ``from_version + 1``."""
        key = (namespace, type, from_version)
        if key in self._upcasts:
            raise DuplicateEventType(f"upcast already registered for {key}")
        self._upcasts[key] = fn

    def upcast(self, namespace: str, type: str, from_version: int) -> Callable[[Upcast], Upcast]:
        """Decorator sugar over :meth:`register`."""

        def decorate(fn: Upcast) -> Upcast:
            self.register(namespace, type, from_version, fn)
            return fn

        return decorate

    def apply(
        self, key: EventKey, payload: dict[str, JsonValue], *, target_version: int
    ) -> tuple[int, dict[str, JsonValue]]:
        """
        Chain upcasts from the stored version toward ``target_version``.

        Returns the version reached and the (possibly transformed) payload. Reaching
        anything short of ``target_version`` raises :class:`UpcastGap`.
        """
        version = key.schema_version
        current = payload
        while version < target_version:
            step = self._upcasts.get((key.namespace, key.type, version))
            if step is None:
                raise UpcastGap(
                    f"no upcast from v{version} for ({key.namespace!r}, {key.type!r}); "
                    f"latest registered schema is v{target_version}"
                )
            current = step(current)
            version += 1
        return version, current


class EventReader:
    """Reads the stream with upcasts applied, optionally decoded to typed events."""

    def __init__(
        self, store: EventStore, upcasts: UpcastRegistry, types: EventTypeRegistry
    ) -> None:
        self._store = store
        self._upcasts = upcasts
        self._types = types

    def read_raw(
        self,
        since_seq: int = 0,
        limit: int | None = None,
        *,
        upto_seq: int | None = None,
        where: Callable[[Envelope], bool] | None = None,
    ) -> Iterator[StoredEvent]:
        """
        Yield stored events with payloads upcast to the latest known version.

        ``upto_seq`` stops the read and ``where`` skips an event, both on the stored
        envelope and *before* any upcast: an event the caller has excluded is never
        touched, so it cannot fail a read it was never part of.
        """
        for event in self._store.read(since_seq, limit):
            if upto_seq is not None and event.seq > upto_seq:
                return
            if where is not None and not where(event.envelope):
                continue
            env = event.envelope
            target = self._types.latest_version(env.namespace, env.type)
            version, payload = self._upcasts.apply(
                EventKey(env.namespace, env.type, env.schema_version),
                event.payload,
                target_version=target,
            )
            if version == env.schema_version:
                yield event
            else:
                yield event.model_copy(
                    update={
                        "payload": payload,
                        "envelope": env.model_copy(update={"schema_version": version}),
                    }
                )

    def read(
        self,
        since_seq: int = 0,
        limit: int | None = None,
        *,
        upto_seq: int | None = None,
        where: Callable[[Envelope], bool] | None = None,
    ) -> Iterator[tuple[StoredEvent, DomainEvent]]:
        """
        Yield ``(stored, decoded)`` pairs: upcast, then validated into the model.

        ``upto_seq`` and ``where`` filter first, as for :meth:`read_raw`.
        """
        for event in self.read_raw(since_seq, limit, upto_seq=upto_seq, where=where):
            env = event.envelope
            key = EventKey(env.namespace, env.type, env.schema_version)
            yield event, self._types.decode(key, event.payload)
