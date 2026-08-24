"""The registry is the only path between raw payloads and typed events."""

import pytest

from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.errors import DuplicateEventType, UnknownEventType
from anagrapheus.timeline.registry import EventKey, EventTypeRegistry


class StepTaken(DomainEvent, frozen=True):
    n: int


class OtherEvent(DomainEvent, frozen=True):
    label: str


def test_register_and_decode_round_trip():
    registry = EventTypeRegistry()
    registry.register(EventKey("demo", "StepTaken", 1), StepTaken)
    pending = registry.pending(StepTaken(n=3))
    assert pending.envelope.namespace == "demo"
    assert pending.envelope.type == "StepTaken"
    assert pending.envelope.schema_version == 1
    decoded = registry.decode(EventKey("demo", "StepTaken", 1), pending.payload)
    assert decoded == StepTaken(n=3)


def test_decorator_registration():
    registry = EventTypeRegistry()

    @registry.event("demo", "Other")
    class Local(DomainEvent, frozen=True):
        pass

    assert registry.key_for(Local) == EventKey("demo", "Other", 1)


def test_duplicate_key_raises():
    registry = EventTypeRegistry()
    registry.register(EventKey("demo", "StepTaken", 1), StepTaken)
    with pytest.raises(DuplicateEventType):
        registry.register(EventKey("demo", "StepTaken", 1), OtherEvent)


def test_duplicate_model_raises():
    registry = EventTypeRegistry()
    registry.register(EventKey("demo", "StepTaken", 1), StepTaken)
    with pytest.raises(DuplicateEventType):
        registry.register(EventKey("demo", "StepTaken", 2), StepTaken)


def test_unregistered_model_raises():
    with pytest.raises(UnknownEventType):
        EventTypeRegistry().pending(StepTaken(n=1))


def test_unknown_key_raises_on_decode():
    with pytest.raises(UnknownEventType):
        EventTypeRegistry().decode(EventKey("demo", "Nope", 1), {})


def test_latest_version():
    registry = EventTypeRegistry()
    registry.register(EventKey("demo", "StepTaken", 1), StepTaken)
    registry.register(EventKey("demo", "StepTaken", 2), OtherEvent)
    assert registry.latest_version("demo", "StepTaken") == 2
    with pytest.raises(UnknownEventType):
        registry.latest_version("demo", "Missing")


def test_causation_and_correlation_pass_through():
    import uuid

    registry = EventTypeRegistry()
    registry.register(EventKey("demo", "StepTaken", 1), StepTaken)
    cause, corr = uuid.uuid7(), uuid.uuid7()
    pending = registry.pending(StepTaken(n=1), causation_id=cause, correlation_id=corr)
    assert pending.envelope.causation_id == cause
    assert pending.envelope.correlation_id == corr


def test_reserved_namespace_rejected_publicly():
    from anagrapheus.timeline.pins import register_builtin_events
    from anagrapheus.timeline.registry import RESERVED_NAMESPACE

    registry = EventTypeRegistry()
    with pytest.raises(ValueError, match="reserved"):
        registry.register(EventKey(RESERVED_NAMESPACE, "Sneaky", 1), StepTaken)
    with pytest.raises(ValueError, match="reserved"):
        registry.event(RESERVED_NAMESPACE, "Sneaky")(OtherEvent)
    register_builtin_events(registry)  # the internal path still works
    assert registry.latest_version(RESERVED_NAMESPACE, "PinPromoted") == 1
