"""
Tier 2: generation-replay from recorded pin ledgers.

Fixtures are log-format JSONL — StoredEvent rows exactly as the events table holds
them, containing PinPromoted events — so ReplayPins reads them with zero translation
and upcasts apply to fixtures for free when pin event schemas evolve.
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

    store = SQLiteEventStore(tmp_path / "new.db")
    replay = ReplayPins.from_events(
        load_ledger(fixture), types, on_miss="record", into=PinLedger(store, types)
    )
    roll, verdict = scripted_unit(replay, subject="doc-8")

    assert roll == 17  # still replayed
    assert verdict is False  # fresh draw: the fresh answer, not the recorded one
    assert replay.divergence is not None
    assert replay.divergence.after_hits == 1


def test_divergence_marked_once_at_first_miss(tmp_path: Path, types: EventTypeRegistry):
    replay = ReplayPins(
        {}, on_miss="record", into=PinLedger(SQLiteEventStore(tmp_path / "t.db"), types)
    )
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
    class DiceRolled(DomainEvent, frozen=True):
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


def test_strict_raises_on_every_miss(types: EventTypeRegistry):
    """Catching one PinMiss must not turn later misses into silent live draws."""
    replay = ReplayPins({}, on_miss="strict")
    for which in ("a", "b"):
        with pytest.raises(PinMiss):
            replay.resolve(
                pin_key("rng", {"which": which}),
                kind="rng",
                effect=lambda: 1,
                interpret=lambda n: n,
            )
    assert replay.divergence is not None
    assert replay.divergence.missed_key == pin_key("rng", {"which": "a"})  # first miss kept


def test_record_mode_pins_fresh_draws(tmp_path: Path, types: EventTypeRegistry):
    """The same missing key resolved twice must not consume entropy twice."""
    replay = ReplayPins(
        {}, on_miss="record", into=PinLedger(SQLiteEventStore(tmp_path / "t.db"), types)
    )
    calls: list[int] = []

    def effect() -> int:
        calls.append(1)
        return len(calls)

    key = pin_key("rng", {"die": "d20"})
    first = replay.resolve(key, kind="rng", effect=effect, interpret=lambda n: n)
    second = replay.resolve(key, kind="rng", effect=effect, interpret=lambda n: n)
    assert (first, second) == (1, 1)
    assert len(calls) == 1


def test_unit_id_with_extract_is_rejected(types: EventTypeRegistry):
    import uuid

    with pytest.raises(ValueError, match="extract"):
        ReplayPins.from_events([], types, unit_id=uuid.uuid7(), extract=lambda stored, event: None)


def test_fixture_events_get_upcast(tmp_path: Path, types: EventTypeRegistry):
    """A v1 fixture keeps working after PinPromoted gains a schema version."""
    from anagrapheus.timeline.pins import PinPromoted
    from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey
    from anagrapheus.timeline.upcast import UpcastRegistry

    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)

    class PinPromotedV2(PinPromoted, frozen=True):
        note: str = ""

    types._register(EventKey(RESERVED_NAMESPACE, "PinPromoted", 2), PinPromotedV2)
    upcasts = UpcastRegistry()
    upcasts.register(RESERVED_NAMESPACE, "PinPromoted", 1, lambda p: {**p, "note": "upcast"})

    replay = ReplayPins.from_events(load_ledger(fixture), types, upcasts=upcasts)
    roll, verdict = scripted_unit(replay)
    assert (roll, verdict) == (17, True)
    assert replay.divergence is None


def test_invalid_on_miss_rejected_at_construction():
    with pytest.raises(ValueError, match="on_miss"):
        ReplayPins({}, on_miss="strcit")  # type: ignore[arg-type]  # the typo is the test


# -- record mode draws into a ledger, so the new run reaches the log ------------------


def _diverged(events: list[StoredEvent]) -> list[StoredEvent]:
    return [e for e in events if e.envelope.type == "ReplayDiverged"]


def test_record_without_a_ledger_is_refused():
    """Its fresh draws would reach no log, so a record-mode run could never replay."""
    with pytest.raises(ValueError, match="into="):
        ReplayPins({}, on_miss="record")


def test_strict_with_a_ledger_is_refused(tmp_path: Path, types: EventTypeRegistry):
    with pytest.raises(ValueError, match="into="):
        ReplayPins({}, into=PinLedger(SQLiteEventStore(tmp_path / "t.db"), types))


def test_record_mode_draws_reach_the_log_at_commit(tmp_path: Path, types: EventTypeRegistry):
    """Fresh draws and adopted hits promote together, with the divergence first."""
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)
    store = SQLiteEventStore(tmp_path / "new.db")
    ledger = PinLedger(store, types)
    replay = ReplayPins.from_events(load_ledger(fixture), types, on_miss="record", into=ledger)
    assert replay.ledger is ledger

    scripted_unit(replay, subject="doc-8")
    assert list(store.read()) == [], "nothing is canon before commit"
    ledger.commit()

    events = list(store.read())
    assert [e.envelope.type for e in events] == ["ReplayDiverged", "PinPromoted", "PinPromoted"]
    diverged = events[0].payload
    assert diverged["unit_id"] == str(ledger.unit_id)
    assert diverged["missed_key"] == pin_key(
        "model_call", {"task": "summarize", "subject": "doc-8"}
    )
    results = {e.payload["kind"]: e.payload["result"] for e in events[1:]}
    assert results == {"rng": 17, "model_call": {"text": "fresh answer"}}, "hit and draw"
    assert store.scratch_list(ledger.unit_id) == []


def test_the_new_log_replays_the_new_run_strictly(tmp_path: Path, types: EventTypeRegistry):
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)
    store = SQLiteEventStore(tmp_path / "new.db")
    ledger = PinLedger(store, types)
    recorded = scripted_unit(
        ReplayPins.from_events(load_ledger(fixture), types, on_miss="record", into=ledger),
        subject="doc-8",
    )
    ledger.commit()

    strict = ReplayPins.from_events(store.read(), types)
    assert scripted_unit(strict, subject="doc-8") == recorded == (17, False)
    assert strict.divergence is None, "no misses: the hits were adopted, not only the draws"


def test_the_divergence_is_promoted_once(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "t.db")
    ledger = PinLedger(store, types)
    replay = ReplayPins({}, on_miss="record", into=ledger)
    for n in (1, 2):
        replay.resolve(pin_key("rng", {"a": n}), kind="rng", effect=lambda n=n: n, interpret=str)
    ledger.commit()
    assert len(_diverged(list(store.read()))) == 1
    ledger.commit()
    assert len(_diverged(list(store.read()))) == 1, "a second commit has nothing new to say"


def test_a_crash_before_commit_leaves_the_run_in_the_scratch(
    tmp_path: Path, types: EventTypeRegistry
):
    """Persist-first, as for a live ledger: a resumed run reuses rather than re-draws."""
    fixture = tmp_path / "unit.jsonl"
    record_ledger(fixture, types)
    path = tmp_path / "new.db"
    store = SQLiteEventStore(path)
    ledger = PinLedger(store, types)
    replay = ReplayPins.from_events(load_ledger(fixture), types, on_miss="record", into=ledger)
    scripted_unit(replay, subject="doc-8")
    store.close()  # the process dies here, before commit

    reopened = SQLiteEventStore(path)
    rows = reopened.scratch_list(ledger.unit_id)
    assert sorted(r.kind for r in rows) == ["model_call", "rng"], "hit and draw both kept"
    assert list(reopened.read()) == [], "and nothing reached the log"

    resumed = PinLedger(reopened, types, unit_id=ledger.unit_id)
    again = ReplayPins.from_events(load_ledger(fixture), types, on_miss="record", into=resumed)
    fresh = pin_key("model_call", {"task": "summarize", "subject": "doc-8"})
    drew: list[int] = []
    answer = again.resolve(
        fresh,
        kind="model_call",
        effect=lambda: drew.append(1) or {"text": "a second draw"},
        interpret=lambda r: r["text"],
    )
    assert (answer, drew) == ("fresh answer", []), "the scratch answered; nothing re-drew"
    resumed.commit()
    assert len(_diverged(list(reopened.read()))) == 1
    reopened.close()


def test_abandoning_a_record_run_drops_its_divergence(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "t.db")
    ledger = PinLedger(store, types)
    replay = ReplayPins({}, on_miss="record", into=ledger)
    replay.resolve(pin_key("rng", {"a": 1}), kind="rng", effect=lambda: 1, interpret=str)
    ledger.abandon(reason="not this branch")
    ledger.commit()
    assert [e.envelope.type for e in store.read()] == ["PinsAbandoned"]


def test_adopt_reuses_what_the_scratch_holds(tmp_path: Path, types: EventTypeRegistry):
    store = SQLiteEventStore(tmp_path / "t.db")
    ledger = PinLedger(store, types)
    key = pin_key("rng", {"die": "d20"})
    assert ledger.adopt(key, kind="rng", result=(1, 2)) == [1, 2]  # concretized
    assert ledger.adopt(key, kind="rng", result=[9]) == [1, 2]  # reused, attempts bumped
    (row,) = store.scratch_list(ledger.unit_id)
    assert (row.kind, row.attempts) == ("rng", 2)
