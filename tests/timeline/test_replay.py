"""
Tier 2: generation-replay from recorded pin ledgers.

Fixtures are log-format JSONL — StoredEvent rows exactly as the events table holds
them, containing PinPromoted events — so ReplayPins reads them with zero translation
and upcasts apply to fixtures for free when pin event schemas evolve. (Provisional
answer to the fixture-format question in _design/00; recorded there.)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.pins import PinLedger, pin_key, register_builtin_events
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.replay import PinMiss, ReplayPins
from anagrapheus.timeline.sqlite import SQLiteEventStore

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline.pins import PinResolver
    from anagrapheus.timeline.store import ScratchRow


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    register_builtin_events(reg)
    return reg


def scripted_unit(resolver: PinResolver, *, subject: str = "doc-7") -> tuple[int, bool]:
    """
    One deterministic unit of work over any resolver — live or replay.

    Draws twice; returns interpreted outcomes only. `subject` perturbs the key
    material of the second draw, simulating a prompt-assembly change.
    """
    roll = resolver.resolve(
        pin_key("rng", {"die": "d20"}),
        kind="rng",
        effect=lambda: 17,
        interpret=lambda n: n,
    )
    verdict = resolver.resolve(
        pin_key("model_call", {"task": "summarize", "subject": subject}),
        kind="model_call",
        effect=lambda: {"text": "fresh answer"},
        interpret=lambda r: r["text"] == "recorded answer",
    )
    return roll, verdict


def record_ledger(path: Path, types: EventTypeRegistry) -> list[StoredEvent]:
    """Test util: run the scripted unit live, commit, dump the log as JSONL."""
    store = SQLiteEventStore(path.with_suffix(".db"))
    ledger = PinLedger(store, types)
    ledger.resolve(
        pin_key("rng", {"die": "d20"}), kind="rng", effect=lambda: 17, interpret=lambda n: n
    )
    ledger.resolve(
        pin_key("model_call", {"task": "summarize", "subject": "doc-7"}),
        kind="model_call",
        effect=lambda: {"text": "recorded answer"},
        interpret=lambda r: r["text"],
    )
    ledger.commit()
    events = list(store.read())
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.model_dump_json() + "\n")
    store.close()
    return events


def load_ledger(path: Path) -> list[StoredEvent]:
    with path.open(encoding="utf-8") as fh:
        return [StoredEvent.model_validate(json.loads(line)) for line in fh if line.strip()]


def test_full_hit_replay_is_identical(tmp_path: Path, types: EventTypeRegistry):
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)

    replay = ReplayPins.from_events(load_ledger(fixture), types)
    roll, verdict = scripted_unit(replay)

    assert (roll, verdict) == (17, True)  # the recorded answer, not the fresh one
    assert replay.divergence is None


def test_perturbed_material_diverges_at_first_miss(tmp_path: Path, types: EventTypeRegistry):
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)

    strict = ReplayPins.from_events(load_ledger(fixture), types)
    with pytest.raises(PinMiss) as excinfo:
        scripted_unit(strict, subject="doc-8")  # prompt assembly changed
    divergence = excinfo.value.divergence
    assert divergence.kind == "model_call"
    assert divergence.after_hits == 1  # the rng pin still hit; the model call missed
    assert strict.divergence == divergence


def test_record_mode_continues_announced(tmp_path: Path, types: EventTypeRegistry):
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)

    replay = ReplayPins.from_events(load_ledger(fixture), types, on_miss="record")
    roll, verdict = scripted_unit(replay, subject="doc-8")

    assert roll == 17  # still replayed
    assert verdict is False  # fresh draw: the fresh answer, not the recorded one
    assert replay.divergence is not None
    assert replay.divergence.after_hits == 1


def test_divergence_marked_once_at_first_miss(types: EventTypeRegistry):
    replay = ReplayPins({}, on_miss="record")
    replay.resolve(pin_key("rng", {"a": 1}), kind="rng", effect=lambda: 1, interpret=lambda n: n)
    first = replay.divergence
    replay.resolve(pin_key("rng", {"a": 2}), kind="rng", effect=lambda: 2, interpret=lambda n: n)
    assert replay.divergence is first  # the second miss does not rewrite history


def test_unit_id_narrowing(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "t.db")
    key = pin_key("rng", {"die": "d20"})

    first = PinLedger(store, types)
    first.resolve(key, kind="rng", effect=lambda: 1, interpret=lambda n: n)
    first.commit()
    second = PinLedger(store, types)
    second.resolve(key, kind="rng", effect=lambda: 2, interpret=lambda n: n)
    second.commit()

    narrowed = ReplayPins.from_events(store.read(), types, unit_id=first.unit_id)
    assert narrowed.resolve(key, kind="rng", effect=lambda: 99, interpret=lambda n: n) == 1


def test_extract_mirrors_promote_as(tmp_path: Path, types: EventTypeRegistry):
    class DiceRolled(DomainEvent):
        pin: str
        roll: int

    types.event("demo", "DiceRolled")(DiceRolled)

    def promote(row: ScratchRow):
        assert isinstance(row.result, int)
        return types.pending(DiceRolled(pin=row.pin_key, roll=row.result))

    store = SQLiteEventStore(tmp_path / "t.db")
    ledger = PinLedger(store, types, promote_as=promote)
    key = pin_key("rng", {"die": "d20"})
    ledger.resolve(key, kind="rng", effect=lambda: 17, interpret=lambda n: n)
    ledger.commit()

    replay = ReplayPins.from_events(
        store.read(),
        types,
        extract=lambda stored, event: (
            (event.pin, event.roll) if isinstance(event, DiceRolled) else None
        ),
    )
    assert replay.resolve(key, kind="rng", effect=lambda: 99, interpret=lambda n: n) == 17
