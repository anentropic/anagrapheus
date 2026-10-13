"""
Milestone-1 acceptance: the whole timeline layer, end to end, one story.

Append -> fold -> crash -> boot replay above the position -> pin -> retry-reuse ->
promote -> abandon-summary -> generation-replay (identical, then perturbed and
announced divergent) -> rebuild equals incremental.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    PinLedger,
    PinsAbandoned,
    Projector,
    ReplayPins,
    SQLiteEventStore,
    StoredEvent,
    UpcastRegistry,
    pin_key,
    register_builtin_events,
)
from anagrapheus.timeline.errors import ConcurrencyError
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline import FoldDeterminism, PinResolver


class StepTaken(DomainEvent, frozen=True):
    n: int


def make_types() -> EventTypeRegistry:
    types = EventTypeRegistry()
    types.event("demo", "StepTaken")(StepTaken)
    register_builtin_events(types)
    return types


class StepCount:
    """Counts StepTaken events into the store's own file; ignores library events."""

    name = "step_count"
    fold: FoldDeterminism = "deterministic"

    def apply(self, event: StoredEvent, txn: object) -> None:
        assert isinstance(txn, sqlite3.Connection)
        txn.execute("CREATE TABLE IF NOT EXISTS counts (id INTEGER PRIMARY KEY, c INTEGER)")
        txn.execute("INSERT INTO counts (id, c) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
        if (event.envelope.namespace, event.envelope.type) == ("demo", "StepTaken"):
            txn.execute("UPDATE counts SET c = c + 1 WHERE id = 1")

    @staticmethod
    def value(store: SQLiteEventStore) -> int:
        with store.transaction() as conn:
            try:
                row = conn.execute("SELECT c FROM counts WHERE id = 1").fetchone()
            except sqlite3.OperationalError:
                return 0
            return int(row["c"]) if row else 0


def scripted_unit(resolver: PinResolver) -> tuple[int, str]:
    roll = resolver.resolve(
        pin_key("rng", {"die": "d20", "purpose": "initiative"}),
        kind="rng",
        effect=lambda: 11,
        interpret=lambda n: n,
    )
    summary = resolver.resolve(
        pin_key("model_call", {"task": "narrate", "subject": "the door"}),
        kind="model_call",
        effect=lambda: {"text": "the door creaks open"},
        interpret=lambda r: str(r["text"]),
    )
    return roll, summary


def test_milestone_1_end_to_end(tmp_path: Path):
    path = tmp_path / "timeline.db"
    types = make_types()

    # 1. Append, with the tripwire.
    store = SQLiteEventStore(path)
    assert store.append([types.pending(StepTaken(n=i)) for i in (1, 2, 3)]) == 3
    with pytest.raises(ConcurrencyError):
        store.append([types.pending(StepTaken(n=4))], expected_last_seq=0)

    # 2. Fold.
    reader = EventReader(store, UpcastRegistry(), types)
    projector = Projector(store, reader, [StepCount()])
    assert projector.catch_up() == {"step_count": 3}
    assert StepCount.value(store) == 3

    # 3. Crash-sim: reopen the file, append more, resume above the position.
    store.close()
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    projector = Projector(store, reader, [StepCount()])
    store.append([types.pending(StepTaken(n=4))])
    projector.catch_up()
    assert StepCount.value(store) == 4  # resumed, never double-counted

    # 4-5. Pin, and reuse under retry.
    ledger = PinLedger(store, types)
    first = scripted_unit(ledger)
    second = scripted_unit(ledger)  # retries: same keys, no fresh draws
    assert first == second == (11, "the door creaks open")
    rows = store.scratch_list(ledger.unit_id)
    assert [(r.kind, r.attempts) for r in rows] == [("rng", 2), ("model_call", 2)]
    assert all(e.envelope.namespace == "demo" for e in store.read())  # nothing canon yet

    # 6. Promote: pins become ordinary events, scratch empties, in one generation.
    last_seq = ledger.commit()
    assert last_seq == 6
    assert store.scratch_list(ledger.unit_id) == []
    promoted = [e for e in store.read() if e.envelope.type == "PinPromoted"]
    assert [e.payload["attempts"] for e in promoted] == [2, 2]

    # 7. Abandon: a second unit leaves exactly one summary event.
    doomed = PinLedger(store, types)
    doomed.resolve(
        pin_key("rng", {"die": "d6"}), kind="rng", effect=lambda: 2, interpret=lambda n: n
    )
    doomed.abandon(reason="validation failed")
    assert store.scratch_list(doomed.unit_id) == []
    summaries = [e for e in store.read() if e.envelope.type == "PinsAbandoned"]
    assert len(summaries) == 1
    decoded = types.decode(EventKey(RESERVED_NAMESPACE, "PinsAbandoned", 1), summaries[0].payload)
    assert isinstance(decoded, PinsAbandoned)
    assert [d.attempts for d in decoded.discarded] == [1]

    # 8. Generation-replay: identical from the log; perturbed input diverges, announced.
    replay = ReplayPins.from_events(reader.read_raw(), types, unit_id=ledger.unit_id)
    assert scripted_unit(replay) == (11, "the door creaks open")
    assert replay.divergence is None

    branch = PinLedger(store, types)
    perturbed = ReplayPins.from_events(reader.read_raw(), types, on_miss="record", into=branch)

    def changed_unit(resolver: PinResolver) -> str:
        return resolver.resolve(
            pin_key("model_call", {"task": "narrate", "subject": "the window"}),
            kind="model_call",
            effect=lambda: {"text": "something new"},
            interpret=lambda r: str(r["text"]),
        )

    assert changed_unit(perturbed) == "something new"  # continued, fresh
    assert perturbed.divergence is not None
    assert perturbed.divergence.kind == "model_call"
    branch.commit()  # the new run, announced in the log with the pins it drew
    announced = [e for e in store.read() if e.envelope.type == "ReplayDiverged"]
    assert [e.payload["unit_id"] for e in announced] == [str(branch.unit_id)]

    # 9. Fold-replay closes the loop: rebuild over the fuller log equals incremental.
    projector.catch_up()  # fold the pin events too (StepCount ignores them)
    incremental = StepCount.value(store)

    def reset() -> None:
        with store.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS counts")

    projector.rebuild("step_count", reset=reset)
    assert StepCount.value(store) == incremental == 4
    store.close()
