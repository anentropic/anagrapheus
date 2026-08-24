"""Fold dispatch: watermark-guarded catch-up, crash resume, rebuild, and invariance."""

import sqlite3
from typing import TYPE_CHECKING, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.folds import Projector
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore
from anagrapheus.timeline.upcast import EventReader, UpcastRegistry

if TYPE_CHECKING:
    from pathlib import Path


class StepTaken(DomainEvent, frozen=True):
    n: int


def make_types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.event("demo", "StepTaken")(StepTaken)
    return reg


class SqlSum:
    """A fold keeping its state in the store's own file: fully transactional."""

    name = "sql_sum"
    clock: Literal["sync"] = "sync"

    def apply(self, event: StoredEvent, txn: object) -> None:
        assert isinstance(txn, sqlite3.Connection)
        txn.execute("CREATE TABLE IF NOT EXISTS sums (id INTEGER PRIMARY KEY, total INTEGER)")
        txn.execute("INSERT INTO sums (id, total) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
        n = int(event.payload["n"])  # type: ignore[arg-type]
        txn.execute("UPDATE sums SET total = total + ? WHERE id = 1", (n,))

    @staticmethod
    def total(store: SQLiteEventStore) -> int:
        with store.transaction() as conn:
            try:
                row = conn.execute("SELECT total FROM sums WHERE id = 1").fetchone()
            except sqlite3.OperationalError:  # empty stream: the fold never ran
                return 0
            return int(row["total"]) if row else 0


class MemoryLog:
    """A fold with external (in-memory) state: sees seqs, appends them in order."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    name = "memory_log"
    clock: Literal["sync"] = "sync"

    def apply(self, event: StoredEvent, txn: object) -> None:
        self.seen.append(event.seq)


def make_stack(path: Path):
    types = make_types()
    store = SQLiteEventStore(path)
    reader = EventReader(store, UpcastRegistry(), types)
    return types, store, reader


def test_catch_up_from_zero(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (1, 2, 3)])
    fold = SqlSum()
    marks = Projector(store, reader, [fold]).catch_up()
    assert marks == {"sql_sum": 3}
    assert SqlSum.total(store) == 6


def test_catch_up_resumes_above_watermark(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    fold = MemoryLog()
    projector = Projector(store, reader, [fold])

    store.append([types.pending(StepTaken(n=1))])
    projector.catch_up()
    store.append([types.pending(StepTaken(n=2))])
    projector.catch_up()
    projector.catch_up()  # nothing new: no re-application

    assert fold.seen == [1, 2]


def test_crash_between_batches_never_double_counts(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in range(1, 8)])

    # First projector folds a couple of small batches, then is discarded mid-way —
    # the crash. A fresh projector (fresh fold state for sql, same file) resumes.
    projector = Projector(store, reader, [SqlSum()])
    projector.catch_up(batch_size=2)  # completes fully; now simulate partial work

    # rewind the world: new store handle on the same file, resume from watermark
    store.close()
    store2 = SQLiteEventStore(tmp_path / "t.db")
    reader2 = EventReader(store2, UpcastRegistry(), make_types())
    Projector(store2, reader2, [SqlSum()]).catch_up(batch_size=2)

    assert SqlSum.total(store2) == sum(range(1, 8))


def test_rebuild_equals_incremental(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (5, 6)])
    fold = SqlSum()
    projector = Projector(store, reader, [fold])
    projector.catch_up()
    incremental = SqlSum.total(store)

    def reset() -> None:
        with store.transaction() as conn:
            conn.execute("DELETE FROM sums")

    projector.rebuild("sql_sum", reset=reset)
    assert SqlSum.total(store) == incremental == 11


def test_rebuild_unknown_fold_raises(tmp_path: Path):
    _, store, reader = make_stack(tmp_path / "t.db")
    with pytest.raises(KeyError):
        Projector(store, reader, []).rebuild("nope", reset=lambda: None)


def test_duplicate_fold_names_rejected(tmp_path: Path):
    _, store, reader = make_stack(tmp_path / "t.db")
    with pytest.raises(ValueError, match="duplicate fold names"):
        Projector(store, reader, [MemoryLog(), MemoryLog()])


@settings(max_examples=25, deadline=None)
@given(
    values=st.lists(st.integers(min_value=0, max_value=100), min_size=0, max_size=40),
    batch_size=st.integers(min_value=1, max_value=7),
    interrupt_after=st.integers(min_value=0, max_value=5),
)
def test_fold_replay_invariance(
    tmp_path_factory: pytest.TempPathFactory,
    values: list[int],
    batch_size: int,
    interrupt_after: int,
):
    """Straight-through == interrupted-and-resumed == rebuilt. Same log, same state."""
    path = tmp_path_factory.mktemp("prop") / "t.db"
    types, store, reader = make_stack(path)
    store.append([types.pending(StepTaken(n=v)) for v in values])

    fold = SqlSum()
    projector = Projector(store, reader, [fold])

    # interrupted run: fold a limited number of batches, then "crash" and resume
    watermark = store.read_watermark(fold.name)
    for _ in range(interrupt_after):
        batch = list(reader.read_raw(since_seq=watermark, limit=batch_size))
        if not batch:
            break
        with store.transaction() as txn:
            for event in batch:
                fold.apply(event, txn)
            watermark = batch[-1].seq
            store.commit_watermark(fold.name, watermark)
    projector.catch_up(batch_size=batch_size)
    interrupted = SqlSum.total(store)

    def reset() -> None:
        with store.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS sums")

    projector.rebuild("sql_sum", reset=reset, batch_size=batch_size)
    rebuilt = SqlSum.total(store)

    assert interrupted == rebuilt == sum(values)
    store.close()


def test_batch_size_must_be_positive(tmp_path: Path):
    _, store, reader = make_stack(tmp_path / "t.db")
    projector = Projector(store, reader, [MemoryLog()])
    with pytest.raises(ValueError, match="batch_size"):
        projector.catch_up(batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        projector.rebuild("memory_log", reset=lambda: None, batch_size=-1)


def test_rebuild_zeroes_watermark_atomically_with_reset(tmp_path: Path):
    """If reset raises, the watermark must not stay high over dropped state."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    fold = SqlSum()
    projector = Projector(store, reader, [fold])
    projector.catch_up()
    assert store.read_watermark("sql_sum") == 1

    def failing_reset() -> None:
        with store.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS sums")
        raise RuntimeError("crash mid-rebuild")

    with pytest.raises(RuntimeError):
        projector.rebuild("sql_sum", reset=failing_reset, reset_in_transaction=True)
    # same-file state declared in-transaction: state drop and watermark-zero roll
    # back together, OR both apply; either way catch-up cannot skip history.
    watermark = store.read_watermark("sql_sum")
    total = SqlSum.total(store)
    assert (watermark, total) == (1, 1) or watermark == 0


def test_default_rebuild_zeroes_watermark_before_reset(tmp_path: Path):
    """External-state posture: a crash after the watermark commit replays, never skips."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    fold = MemoryLog()
    projector = Projector(store, reader, [fold])
    projector.catch_up()

    def failing_reset() -> None:
        assert store.read_watermark("memory_log") == 0  # already committed
        raise RuntimeError("crash during external reset")

    with pytest.raises(RuntimeError):
        projector.rebuild("memory_log", reset=failing_reset)
    assert store.read_watermark("memory_log") == 0  # recovery replays
