"""Pin lifecycle: reuse under retry, promote, abandon, crash recovery, key stability."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from anagrapheus.timeline.pins import (
    PinLedger,
    PinPromoted,
    PinsAbandoned,
    pin_key,
    register_builtin_events,
)
from anagrapheus.timeline.registry import EventKey, EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

    from anagrapheus.timeline.store import ScratchRow


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    register_builtin_events(reg)
    return reg


@pytest.fixture
def store(tmp_path: Path) -> SQLiteEventStore:
    return SQLiteEventStore(tmp_path / "t.db")


class Effect:
    """A counting effect: proves how many times entropy was actually consumed."""

    def __init__(self, results: list[dict[str, int]] | None = None) -> None:
        self.calls = 0
        self._results = results

    def __call__(self) -> dict[str, int]:
        self.calls += 1
        if self._results is not None:
            return self._results[self.calls - 1]
        return {"draw": self.calls}


def test_same_key_reuses_across_retries(store: SQLiteEventStore, types: EventTypeRegistry):
    ledger = PinLedger(store, types)
    effect = Effect(results=[{"n": 1}, {"n": 999}])
    key = pin_key("rng", {"die": "d20"})

    outcomes = [
        ledger.resolve(key, kind="rng", effect=effect, interpret=lambda r: r["n"]) for _ in range(3)
    ]
    assert outcomes == [1, 1, 1]  # the 999 was never drawn
    assert effect.calls == 1
    (row,) = store.scratch_list(ledger.unit_id)
    assert row.attempts == 3


def test_different_key_draws_fresh(store: SQLiteEventStore, types: EventTypeRegistry):
    ledger = PinLedger(store, types)
    effect = Effect()
    for die in ("d20", "d6"):
        ledger.resolve(
            pin_key("rng", {"die": die}), kind="rng", effect=effect, interpret=lambda r: r
        )
    assert effect.calls == 2


def test_interpretation_change_reuses_pinned_raw(store: SQLiteEventStore, types: EventTypeRegistry):
    """Re-reading a result must not re-draw it — and the caller never sees the raw."""
    ledger = PinLedger(store, types)
    effect = Effect(results=[{"roll": 14}, {"roll": 2}])
    key = pin_key("rng", {"die": "d20", "purpose": "attack"})

    hit = ledger.resolve(key, kind="rng", effect=effect, interpret=lambda r: r["roll"] > 10)
    graze = ledger.resolve(key, kind="rng", effect=effect, interpret=lambda r: r["roll"] > 13)
    assert (hit, graze) == (True, True)  # same pinned 14, judged twice
    assert effect.calls == 1


def test_commit_promotes_and_clears_atomically(store: SQLiteEventStore, types: EventTypeRegistry):
    ledger = PinLedger(store, types)
    key = pin_key("fetch", {"url": "https://example.com"})
    ledger.resolve(key, kind="fetch", effect=lambda: {"status": 200}, interpret=lambda r: r)
    ledger.resolve(key, kind="fetch", effect=lambda: {"status": 500}, interpret=lambda r: r)

    last_seq = ledger.commit()
    assert last_seq == 1
    assert store.scratch_list(ledger.unit_id) == []

    (stored,) = list(store.read())
    assert stored.envelope.namespace == "anagrapheus"
    promoted = types.decode(EventKey("anagrapheus", "PinPromoted", 1), stored.payload)
    assert isinstance(promoted, PinPromoted)
    assert promoted.key == key
    assert promoted.result == {"status": 200}
    assert promoted.attempts == 2


def test_commit_with_nothing_pinned(store: SQLiteEventStore, types: EventTypeRegistry):
    assert PinLedger(store, types).commit() == 0
    assert list(store.read()) == []


def test_abandon_leaves_one_summary(store: SQLiteEventStore, types: EventTypeRegistry):
    ledger = PinLedger(store, types)
    for name in ("a", "b"):
        key = pin_key("rng", {"which": name})
        ledger.resolve(key, kind="rng", effect=lambda: 1, interpret=lambda r: r)
        ledger.resolve(key, kind="rng", effect=lambda: 2, interpret=lambda r: r)

    last_seq = ledger.abandon(reason="validation failed")
    assert last_seq == 1
    assert store.scratch_list(ledger.unit_id) == []

    (stored,) = list(store.read())
    summary = types.decode(EventKey("anagrapheus", "PinsAbandoned", 1), stored.payload)
    assert isinstance(summary, PinsAbandoned)
    assert summary.reason == "validation failed"
    assert [(d.kind, d.attempts) for d in summary.discarded] == [("rng", 2), ("rng", 2)]


def test_scratch_survives_crash(tmp_path: Path, types: EventTypeRegistry):
    """A crash mid-unit leaves the pins; a resumed unit reuses instead of re-drawing."""
    path = tmp_path / "t.db"
    first = SQLiteEventStore(path)
    ledger = PinLedger(first, types)
    key = pin_key("model_call", {"prompt": "hello"})
    ledger.resolve(key, kind="model_call", effect=lambda: {"text": "hi"}, interpret=lambda r: r)
    unit_id = ledger.unit_id
    first.close()  # crash: no commit, no abandon

    second = SQLiteEventStore(path)
    resumed = PinLedger(second, types, unit_id=unit_id)
    effect = Effect()
    result = resumed.resolve(key, kind="model_call", effect=effect, interpret=lambda r: r)
    assert result == {"text": "hi"}
    assert effect.calls == 0
    assert list(second.read()) == []  # still nothing canon


def test_promote_as_maps_into_app_vocabulary(store: SQLiteEventStore, types: EventTypeRegistry):
    from anagrapheus.timeline.envelope import DomainEvent

    class DiceRolled(DomainEvent):
        roll: int

    types.event("demo", "DiceRolled")(DiceRolled)

    def promote(row: ScratchRow):
        assert isinstance(row.result, int)
        return types.pending(DiceRolled(roll=row.result))

    ledger = PinLedger(store, types, promote_as=promote)
    ledger.resolve(pin_key("rng", {"d": 20}), kind="rng", effect=lambda: 17, interpret=lambda r: r)
    ledger.commit()

    (stored,) = list(store.read())
    assert (stored.envelope.namespace, stored.envelope.type) == ("demo", "DiceRolled")
    assert stored.payload == {"roll": 17}


# -- key stability -------------------------------------------------------------

json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.text(max_size=20),
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=10,
)
materials = st.dictionaries(st.text(min_size=1, max_size=10), json_values, max_size=5)


@given(material=materials)
def test_pin_key_is_deterministic(material: dict[str, JsonValue]):
    assert pin_key("k", material) == pin_key("k", material)


@given(material=materials)
def test_pin_key_ignores_insertion_order(material: dict[str, JsonValue]):
    reordered = dict(reversed(list(material.items())))
    assert pin_key("k", material) == pin_key("k", reordered)


@given(material=materials)
def test_pin_key_scheme_bump_rekeys(material: dict[str, JsonValue]):
    assert pin_key("k", material, scheme=1) != pin_key("k", material, scheme=2)


@given(material=materials, extra=st.text(min_size=1, max_size=10))
def test_pin_key_material_change_rekeys(material: dict[str, JsonValue], extra: str):
    changed: dict[str, JsonValue] = {**material, extra: "\x00sentinel"}
    assert pin_key("k", material) != pin_key("k", changed)


def test_pin_key_kind_separates():
    assert pin_key("rng", {"a": 1}) != pin_key("fetch", {"a": 1})
