"""The SQLite adapter honors the EventStore port's contract."""

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from anagrapheus.timeline.envelope import DomainEvent
from anagrapheus.timeline.errors import ConcurrencyError
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore


class StepTaken(DomainEvent, frozen=True):
    n: int


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.event("demo", "StepTaken")(StepTaken)
    return reg


@pytest.fixture
def store(tmp_path: Path) -> SQLiteEventStore:
    return SQLiteEventStore(tmp_path / "timeline.db")


def test_empty_stream(store: SQLiteEventStore):
    assert store.last_seq() == 0
    assert list(store.read()) == []


def test_append_read_round_trip(store: SQLiteEventStore, types: EventTypeRegistry):
    batch = [types.pending(StepTaken(n=1)), types.pending(StepTaken(n=2))]
    assert store.append(batch) == 2
    events = list(store.read())
    assert [e.seq for e in events] == [1, 2]
    assert [e.payload for e in events] == [{"n": 1}, {"n": 2}]
    assert events[0].envelope == batch[0].envelope


def test_seq_continues_across_batches(store: SQLiteEventStore, types: EventTypeRegistry):
    store.append([types.pending(StepTaken(n=1))])
    assert store.append([types.pending(StepTaken(n=2))]) == 2
    assert store.last_seq() == 2


def test_read_since_and_limit(store: SQLiteEventStore, types: EventTypeRegistry):
    store.append([types.pending(StepTaken(n=i)) for i in range(5)])
    assert [e.seq for e in store.read(since_seq=2)] == [3, 4, 5]
    assert [e.seq for e in store.read(since_seq=1, limit=2)] == [2, 3]


def test_concurrency_tripwire(store: SQLiteEventStore, types: EventTypeRegistry):
    store.append([types.pending(StepTaken(n=1))])
    with pytest.raises(ConcurrencyError):
        store.append([types.pending(StepTaken(n=2))], expected_last_seq=0)
    # nothing was written by the failed append
    assert store.last_seq() == 1
    assert store.append([types.pending(StepTaken(n=2))], expected_last_seq=1) == 2


def test_empty_batch_is_a_noop(store: SQLiteEventStore):
    assert store.append([]) == 0
    assert store.last_seq() == 0


def test_positions(store: SQLiteEventStore):
    assert store.read_position("counts") == 0
    store.commit_position("counts", 7)
    assert store.read_position("counts") == 7
    store.commit_position("counts", 9)
    assert store.read_position("counts") == 9
    assert store.read_position("other") == 0


def test_persistence_across_reopen(tmp_path: Path, types: EventTypeRegistry):
    path = tmp_path / "timeline.db"
    first = SQLiteEventStore(path)
    first.append([types.pending(StepTaken(n=1))])
    first.commit_position("counts", 1)
    first.close()

    second = SQLiteEventStore(path)
    assert second.last_seq() == 1
    assert second.read_position("counts") == 1
    assert [e.payload for e in second.read()] == [{"n": 1}]


def test_atomicity_under_midbatch_failure(store: SQLiteEventStore, types: EventTypeRegistry):
    good = types.pending(StepTaken(n=1))
    duplicate_id = good.model_copy()  # same event_id violates the UNIQUE constraint
    with pytest.raises(sqlite3.IntegrityError):
        store.append([good, duplicate_id])
    assert store.last_seq() == 0
    assert list(store.read()) == []


def test_nested_failure_rolls_back_only_its_savepoint(
    store: SQLiteEventStore, types: EventTypeRegistry
):
    """A caught nested failure must not let the outer commit persist partial writes."""
    store.append([types.pending(StepTaken(n=1))])
    good = types.pending(StepTaken(n=2))
    with store.transaction():
        store.commit_position("outer", 1)
        with pytest.raises(sqlite3.IntegrityError):
            store.append([good, good.model_copy()])  # partial insert, then UNIQUE fails
        # outer scope continues after catching the nested failure
    assert store.last_seq() == 1  # the nested append left nothing behind
    assert store.read_position("outer") == 1  # the outer work committed
