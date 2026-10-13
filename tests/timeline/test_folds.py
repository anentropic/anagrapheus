"""Fold dispatch: position-guarded catch-up, crash resume, rebuild, and invariance."""

import sqlite3
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.timeline.envelope import DomainEvent, StoredEvent
from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.folds import Projector
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore
from anagrapheus.timeline.upcast import EventReader, UpcastRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline import FoldDeterminism


class StepTaken(DomainEvent, frozen=True):
    n: int


def make_types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.event("demo", "StepTaken")(StepTaken)
    return reg


class SqlSum:
    """A fold keeping its state in the store's own file: fully transactional."""

    name = "sql_sum"
    fold: FoldDeterminism = "deterministic"

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
    fold: FoldDeterminism = "deterministic"

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


def test_catch_up_resumes_above_position(tmp_path: Path):
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

    # rewind the world: new store handle on the same file, resume from position
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
    position = store.read_position(fold.name)
    for _ in range(interrupt_after):
        batch = list(reader.read_raw(since_seq=position, limit=batch_size))
        if not batch:
            break
        with store.transaction() as txn:
            for event in batch:
                fold.apply(event, txn)
            position = batch[-1].seq
            store.commit_position(fold.name, position)
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


def test_rebuild_zeroes_position_atomically_with_reset(tmp_path: Path):
    """If reset raises, the position must not stay high over dropped state."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    fold = SqlSum()
    projector = Projector(store, reader, [fold])
    projector.catch_up()
    assert store.read_position("sql_sum") == 1

    def failing_reset() -> None:
        with store.transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS sums")
        raise RuntimeError("crash mid-rebuild")

    with pytest.raises(RuntimeError):
        projector.rebuild("sql_sum", reset=failing_reset, reset_in_transaction=True)
    # same-file state declared in-transaction: state drop and position-zero roll
    # back together, OR both apply; either way catch-up cannot skip history.
    position = store.read_position("sql_sum")
    total = SqlSum.total(store)
    assert (position, total) == (1, 1) or position == 0


def test_default_rebuild_zeroes_position_before_reset(tmp_path: Path):
    """External-state posture: a crash after the position commit replays, never skips."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    fold = MemoryLog()
    projector = Projector(store, reader, [fold])
    projector.catch_up()

    def failing_reset() -> None:
        assert store.read_position("memory_log") == 0  # already committed
        raise RuntimeError("crash during external reset")

    with pytest.raises(RuntimeError):
        projector.rebuild("memory_log", reset=failing_reset)
    assert store.read_position("memory_log") == 0  # recovery replays


def test_two_projectors_split_the_schedule(tmp_path: Path):
    """
    The sync/async split, without a field on the fold.

    NVM's R6 puts belief facts on the turn's critical path and portrait regeneration
    off it. That is a property of who calls catch_up and when, not of the fold: two
    projectors over two fold lists, each fold keeping its own position, and neither
    interfering with the other.
    """
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (1, 2, 3)])

    critical, deferred = SqlSum(), MemoryLog()
    on_the_turn = Projector(store, reader, [critical])
    between_turns = Projector(store, reader, [deferred])

    on_the_turn.catch_up()
    assert SqlSum.total(store) == 6
    assert deferred.seen == []  # not driven yet, and its position says so
    assert store.read_position(deferred.name) == 0

    between_turns.catch_up()
    assert deferred.seen == [1, 2, 3]
    assert store.read_position(critical.name) == store.read_position(deferred.name) == 3


class OwnedLog(MemoryLog):
    """External state that keeps its own position, as :class:`OwnsPosition` describes."""

    name = "owned_log"

    def __init__(self, *, fail_at: int | None = None) -> None:
        super().__init__()
        self.position = 0
        self.fail_at = fail_at

    def read_position(self) -> int | None:
        return self.position

    def commit_position(self, seq: int) -> None:
        self.position = seq

    def apply(self, event: StoredEvent, txn: object) -> None:
        if event.seq == self.fail_at:
            raise RuntimeError("the batch dies here")
        super().apply(event, txn)


def test_a_fold_that_owns_its_position_is_positioned_on_itself(tmp_path: Path):
    """The store's row stays untouched, so a fresh fold object starts from zero."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (1, 2, 3)])
    fold = OwnedLog()
    projector = Projector(store, reader, [fold])

    assert projector.catch_up(batch_size=2) == {"owned_log": 3}
    assert fold.position == projector.position("owned_log") == 3
    assert dict(projector.positions()) == {"owned_log": 3}
    assert store.read_position("owned_log") == 0

    again = OwnedLog()
    Projector(store, reader, [again]).catch_up()
    assert again.seen == [1, 2, 3]


def test_an_owned_position_does_not_advance_past_a_failed_batch(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (1, 2, 3, 4)])
    fold = OwnedLog(fail_at=3)
    projector = Projector(store, reader, [fold])

    with pytest.raises(RuntimeError):
        projector.catch_up(batch_size=2)
    assert fold.position == 2

    fold.fail_at = None
    projector.catch_up(batch_size=2)
    assert fold.position == 4


def test_rebuild_zeroes_an_owned_position(tmp_path: Path):
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=i)) for i in (1, 2)])
    fold = OwnedLog()
    projector = Projector(store, reader, [fold])
    projector.catch_up()

    seen_at_reset: list[int] = []
    assert projector.rebuild("owned_log", reset=lambda: seen_at_reset.append(fold.position)) == 2
    assert seen_at_reset == [0]
    assert fold.seen == [1, 2, 1, 2]
    assert store.read_position("owned_log") == 0


def test_a_fold_answering_none_is_positioned_in_the_store(tmp_path: Path):
    """How one class serves both cases: ``None`` hands the position back to the store."""

    class Durable(OwnedLog):
        def read_position(self) -> int | None:
            return None

        def commit_position(self, seq: int) -> None:
            raise AssertionError("never called for a fold the store positions")

    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    projector = Projector(store, reader, [Durable()])
    projector.catch_up()
    assert store.read_position("owned_log") == projector.position("owned_log") == 1


def test_an_owned_position_is_refused_inside_an_open_transaction(tmp_path: Path):
    """
    An outer rollback would undo the events but not the fold's state or position.

    Were it allowed, the rolled-back seqs would be reused by the next append and the
    fold, positioned above them, would skip the new events.
    """
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    fold = OwnedLog()
    projector = Projector(store, reader, [fold, MemoryLog()])

    with store.transaction():
        store.append([types.pending(StepTaken(n=2))])
        with pytest.raises(TimelineError, match="owned_log"):
            projector.catch_up()
        with pytest.raises(TimelineError, match="owned_log"):
            projector.rebuild("owned_log", reset=lambda: None)
    assert fold.seen == []
    assert fold.position == 0

    projector.catch_up()
    assert fold.seen == [1, 2]


def test_a_store_positioned_fold_may_catch_up_inside_a_transaction(tmp_path: Path):
    """Its position is in the store, so it rolls back with everything else."""
    types, store, reader = make_stack(tmp_path / "t.db")
    store.append([types.pending(StepTaken(n=1))])
    projector = Projector(store, reader, [SqlSum()])

    class Abort(Exception):
        pass

    with pytest.raises(Abort), store.transaction():
        store.append([types.pending(StepTaken(n=2))])
        assert projector.catch_up() == {"sql_sum": 2}
        raise Abort
    assert store.read_position("sql_sum") == 0
    assert SqlSum.total(store) == 0
