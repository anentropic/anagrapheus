"""Upcast-on-read: pure chains, loud gaps, untouched storage."""

from typing import TYPE_CHECKING

import pytest

from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.errors import DuplicateEventType, UpcastGap
from anagrapheus.timeline.registry import EventKey, EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore
from anagrapheus.timeline.upcast import EventReader, UpcastRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue


class StepV1(DomainEvent):
    n: int


class StepV3(DomainEvent):
    n: int
    label: str
    source: str


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.register(EventKey("demo", "Step", 1), StepV1)
    reg.register(EventKey("demo", "Step", 3), StepV3)
    return reg


@pytest.fixture
def upcasts() -> UpcastRegistry:
    reg = UpcastRegistry()
    reg.register("demo", "Step", 1, lambda p: {**p, "label": f"step-{p['n']}"})
    reg.register("demo", "Step", 2, lambda p: {**p, "source": "upcast"})
    return reg


def test_decorator_registers_and_returns_fn():
    reg = UpcastRegistry()

    def widen(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return payload

    assert reg.upcast("demo", "Step", 1)(widen) is widen
    with pytest.raises(DuplicateEventType):
        reg.register("demo", "Step", 1, widen)


def test_chain_applies_in_order(upcasts: UpcastRegistry):
    version, payload = upcasts.apply(EventKey("demo", "Step", 1), {"n": 1}, target_version=3)
    assert version == 3
    assert payload == {"n": 1, "label": "step-1", "source": "upcast"}


def test_already_latest_is_untouched(upcasts: UpcastRegistry):
    version, payload = upcasts.apply(EventKey("demo", "Step", 3), {"n": 1}, target_version=3)
    assert (version, payload) == (3, {"n": 1})


def test_gap_raises(types: EventTypeRegistry):
    sparse = UpcastRegistry()
    sparse.register("demo", "Step", 2, lambda p: p)  # v1 step missing
    with pytest.raises(UpcastGap):
        sparse.apply(EventKey("demo", "Step", 1), {"n": 1}, target_version=3)


def test_duplicate_upcast_raises(upcasts: UpcastRegistry):
    with pytest.raises(DuplicateEventType):
        upcasts.register("demo", "Step", 1, lambda p: p)


def test_reader_upcasts_and_decodes(
    tmp_path: Path, types: EventTypeRegistry, upcasts: UpcastRegistry
):
    store = SQLiteEventStore(tmp_path / "t.db")
    store.append([types.pending(StepV1(n=7))])

    reader = EventReader(store, upcasts, types)
    ((stored, decoded),) = list(reader.read())
    assert stored.envelope.schema_version == 3
    assert decoded == StepV3(n=7, label="step-7", source="upcast")


def test_stored_payload_never_rewritten(
    tmp_path: Path, types: EventTypeRegistry, upcasts: UpcastRegistry
):
    store = SQLiteEventStore(tmp_path / "t.db")
    store.append([types.pending(StepV1(n=7))])
    reader = EventReader(store, upcasts, types)

    list(reader.read())  # exercise the upcast path
    raw = list(store.read())  # then look underneath
    assert raw[0].envelope.schema_version == 1
    assert raw[0].payload == {"n": 7}
