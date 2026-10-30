"""The log's hash chain: computed on append, backfilled on upgrade, checked by verify."""

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from anagrapheus.timeline.chain import ChainBroken, event_hash, verify_chain
from anagrapheus.timeline.envelope import DomainEvent, Envelope, PendingEvent, StoredEvent
from anagrapheus.timeline.migrations import COMPONENT, MIGRATIONS
from anagrapheus.timeline.recording import read_recording, write_recording
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore


class StepTaken(DomainEvent, frozen=True):
    n: int


class Said(DomainEvent, frozen=True):
    text: str


@pytest.fixture
def types() -> EventTypeRegistry:
    reg = EventTypeRegistry()
    reg.event("demo", "StepTaken")(StepTaken)
    reg.event("demo", "Said")(Said)
    return reg


@pytest.fixture
def store(tmp_path: Path) -> SQLiteEventStore:
    return SQLiteEventStore(tmp_path / "timeline.db")


def _fixed_envelope() -> Envelope:
    return Envelope(
        event_id=UUID("01a13299-53e2-733b-ac71-2989151b18bc"),
        namespace="demo",
        type="StepTaken",
        schema_version=1,
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )


def _tamper(store: SQLiteEventStore, sql: str, *params: object) -> None:
    store.connection.execute(sql, params)


class TestEventHash:
    def test_known_vector(self):
        """The scheme is a format: a change to what is hashed must fail this, not pass."""
        first = event_hash(None, _fixed_envelope(), {"n": 1})
        assert first == "d42508a69d6292fc436fe6353930cdf8a15b465d54f12920b0e9f4f6c7f00713"
        second = event_hash(first, _fixed_envelope(), {"n": 1})
        assert second == "905c1278c2698afb07c022437df73567754e41f09ff383d41d1a44e4f4dba029"

    def test_payload_key_order_does_not_matter(self):
        env = _fixed_envelope()
        assert event_hash(None, env, {"a": 1, "b": 2}) == event_hash(None, env, {"b": 2, "a": 1})

    def test_strings_are_not_normalized(self):
        """Unlike a pin key, a composed and a decomposed é are different bytes, so differ."""
        env = _fixed_envelope()
        assert event_hash(None, env, {"t": "é"}) != event_hash(None, env, {"t": "é"})

    def test_every_envelope_field_counts(self):
        env = _fixed_envelope()
        base = event_hash(None, env, {})
        variants = [
            env.model_copy(update={"namespace": "other"}),
            env.model_copy(update={"type": "Other"}),
            env.model_copy(update={"schema_version": 2}),
            env.model_copy(update={"causation_id": env.event_id}),
            env.model_copy(update={"correlation_id": env.event_id}),
            env.model_copy(update={"created_at": datetime(2026, 10, 30, 12, 1, tzinfo=UTC)}),
        ]
        hashes = {event_hash(None, v, {}) for v in variants}
        assert base not in hashes
        assert len(hashes) == len(variants)


class TestAppend:
    def test_every_stored_event_has_a_hash(self, store: SQLiteEventStore, types: EventTypeRegistry):
        store.append([types.pending(StepTaken(n=i)) for i in range(3)])
        events = list(store.read())
        assert all(e.hash is not None for e in events)
        assert verify_chain(events) == events[-1].hash

    def test_first_event_chains_from_nothing(
        self, store: SQLiteEventStore, types: EventTypeRegistry
    ):
        pending = types.pending(StepTaken(n=1))
        store.append([pending])
        (stored,) = store.read()
        assert stored.hash == event_hash(None, pending.envelope, pending.payload)

    def test_empty_log_has_no_head(self, store: SQLiteEventStore):
        assert verify_chain(store.read()) is None

    def test_batches_chain_as_one_batch_would(self, tmp_path: Path, types: EventTypeRegistry):
        """How the events were batched leaves no mark on the chain."""
        batch = [types.pending(StepTaken(n=i)) for i in range(4)]
        together = SQLiteEventStore(tmp_path / "together.db")
        together.append(batch)
        apart = SQLiteEventStore(tmp_path / "apart.db")
        apart.append(batch[:1])
        apart.append(batch[1:3])
        apart.append(batch[3:])
        assert [e.hash for e in together.read()] == [e.hash for e in apart.read()]

    def test_a_verbatim_copy_holds_the_same_hashes(self, tmp_path: Path, types: EventTypeRegistry):
        """A copy into a fresh log (a fork's prefix, say) reproduces the parent's chain."""
        parent = SQLiteEventStore(tmp_path / "parent.db")
        parent.append([types.pending(StepTaken(n=i)) for i in range(3)])
        copied = [PendingEvent(envelope=e.envelope, payload=e.payload) for e in parent.read()]
        child = SQLiteEventStore(tmp_path / "child.db")
        child.append(copied[:2])
        child_events = list(child.read())
        assert [e.hash for e in child_events] == [e.hash for e in parent.read(limit=2)]

    def test_hashes_survive_reopen(self, tmp_path: Path, types: EventTypeRegistry):
        path = tmp_path / "timeline.db"
        first = SQLiteEventStore(path)
        first.append([types.pending(Said(text="café \U0001f600"))])
        before = [e.hash for e in first.read()]
        first.close()
        second = SQLiteEventStore(path)
        assert [e.hash for e in second.read()] == before
        assert verify_chain(second.read()) == before[-1]

    def test_failed_append_leaves_the_head_alone(
        self, store: SQLiteEventStore, types: EventTypeRegistry
    ):
        store.append([types.pending(StepTaken(n=1))])
        head = verify_chain(store.read())
        good = types.pending(StepTaken(n=2))
        with pytest.raises(sqlite3.IntegrityError):
            store.append([good, good.model_copy()])
        assert verify_chain(store.read()) == head


class TestVerify:
    @pytest.fixture
    def filled(self, store: SQLiteEventStore, types: EventTypeRegistry) -> SQLiteEventStore:
        store.append([types.pending(StepTaken(n=i)) for i in range(1, 5)])
        return store

    def test_an_edited_payload_breaks_at_that_event(self, filled: SQLiteEventStore):
        _tamper(filled, "UPDATE events SET payload = ? WHERE seq = 2", '{"n": 99}')
        with pytest.raises(ChainBroken) as caught:
            verify_chain(filled.read())
        assert caught.value.seq == 2

    def test_an_edited_envelope_breaks_at_that_event(self, filled: SQLiteEventStore):
        _tamper(
            filled, "UPDATE events SET created_at = ? WHERE seq = 3", "2000-01-01T00:00:00+00:00"
        )
        with pytest.raises(ChainBroken) as caught:
            verify_chain(filled.read())
        assert caught.value.seq == 3

    def test_a_removed_event_breaks_at_the_next(self, filled: SQLiteEventStore):
        _tamper(filled, "DELETE FROM events WHERE seq = 2")
        with pytest.raises(ChainBroken) as caught:
            verify_chain(filled.read())
        assert caught.value.seq == 3

    def test_swapped_events_break(self, filled: SQLiteEventStore):
        _tamper(filled, "UPDATE events SET seq = 100 WHERE seq = 2")
        _tamper(filled, "UPDATE events SET seq = 2 WHERE seq = 3")
        _tamper(filled, "UPDATE events SET seq = 3 WHERE seq = 100")
        with pytest.raises(ChainBroken) as caught:
            verify_chain(filled.read())
        assert caught.value.seq == 2

    def test_a_cut_tail_still_verifies(self, filled: SQLiteEventStore):
        """The chain alone cannot see truncation; a head kept elsewhere can."""
        head = verify_chain(filled.read())
        _tamper(filled, "DELETE FROM events WHERE seq = 4")
        assert verify_chain(filled.read()) != head

    def test_part_way_through_given_the_previous_hash(self, filled: SQLiteEventStore):
        (second,) = filled.read(since_seq=1, limit=1)
        assert verify_chain(filled.read(since_seq=2), prev=second.hash) == verify_chain(
            filled.read()
        )
        with pytest.raises(ChainBroken):
            verify_chain(filled.read(since_seq=2))

    def test_an_event_without_a_hash_is_broken(self):
        event = StoredEvent(seq=1, envelope=_fixed_envelope(), payload={"n": 1})
        with pytest.raises(ChainBroken) as caught:
            verify_chain([event])
        assert caught.value.found is None


class TestUpgrade:
    def test_a_v1_log_is_chained_as_append_would_have(
        self, tmp_path: Path, types: EventTypeRegistry
    ):
        batch = [types.pending(StepTaken(n=1)), types.pending(Said(text="hello"))]
        batch.append(types.pending(StepTaken(n=2), causation_id=batch[0].envelope.event_id))
        fresh = SQLiteEventStore(tmp_path / "fresh.db")
        fresh.append(batch)
        expected = [e.hash for e in fresh.read()]

        path = tmp_path / "v1.db"
        conn = sqlite3.connect(path, autocommit=True)
        conn.execute(
            "CREATE TABLE schema_versions (component TEXT PRIMARY KEY,"
            " version INTEGER NOT NULL, updated_at TEXT NOT NULL) STRICT"
        )
        for statement in MIGRATIONS[0]:
            assert isinstance(statement, str)
            conn.execute(statement)
        conn.execute("INSERT INTO schema_versions VALUES (?, 1, '')", (COMPONENT,))
        rows = fresh.connection.execute(
            "SELECT seq, event_id, namespace, type, schema_version, causation_id,"
            " correlation_id, created_at, payload FROM events ORDER BY seq"
        ).fetchall()
        conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.close()

        upgraded = SQLiteEventStore(path)
        assert [e.hash for e in upgraded.read()] == expected
        upgraded.append([types.pending(StepTaken(n=3))])
        assert verify_chain(upgraded.read()) is not None


class TestRecording:
    def test_a_recording_verifies(
        self, store: SQLiteEventStore, types: EventTypeRegistry, tmp_path: Path
    ):
        """Recorded events keep every byte, so a decomposed é still matches its hash."""
        store.append([types.pending(Said(text="caf" + "é")), types.pending(StepTaken(n=1))])
        path = tmp_path / "run.jsonl"
        write_recording(path, store.read(), header={})
        recorded = read_recording(path).events
        assert recorded[0].payload == {"text": "café"}
        assert verify_chain(recorded) == verify_chain(store.read())
