"""
A recorded run on disk, in the log's own format.

Refused when it is not one, and still readable after the schema it was written under
has moved on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import JsonValue  # noqa: TC002 -- pydantic resolves event annotations at runtime

from anagrapheus import register_builtin_events
from anagrapheus.timeline import (
    DomainEvent,
    EventKey,
    EventTypeRegistry,
    PinLedger,
    RecordingError,
    ReplayPins,
    SQLiteEventStore,
    UpcastRegistry,
    pin_events,
    pin_key,
    read_recording,
    write_recording,
)

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline import StoredEvent


class Noted(DomainEvent, frozen=True):
    """Something the run did, as opposed to something it drew."""

    what: str


@pytest.fixture
def types() -> EventTypeRegistry:
    registry = EventTypeRegistry()
    register_builtin_events(registry)
    registry.register(EventKey("app", "Noted", 1), Noted)
    return registry


def _drawn(tmp_path: Path, types: EventTypeRegistry) -> tuple[list[StoredEvent], list[int]]:
    """A run that pins two draws and commits them, plus a counter of real draws."""
    store = SQLiteEventStore(tmp_path / "run.db")
    ledger = PinLedger(store, types)
    draws: list[int] = []

    def effect(n: int) -> int:
        draws.append(n)
        return n * 10

    for n in (1, 2):
        ledger.resolve(
            pin_key("demo", {"n": n}), kind="demo", effect=lambda n=n: effect(n), interpret=int
        )
    ledger.commit()
    # A real run does work as well as drawing. Without this the store held nothing but
    # pins, `pin_events` had nothing to drop, and a filter that kept everything passed
    # every test that mentioned it -- note 06 §2e's shape, in a test written for B.4.
    store.append([types.pending(Noted(what="the round closed"))])
    events = list(store.read())
    store.close()
    return events, draws


def test_a_pin_corpus_written_to_disk_replays_without_drawing(
    tmp_path: Path, types: EventTypeRegistry
):
    """
    Recorded, written to disk, read back, and replayed strictly without drawing.

    The claim no test here made before: the reader shares nothing with the run that
    recorded it but the bytes. Every earlier replay was built from a live store in the
    same test that recorded it.
    """
    events, draws = _drawn(tmp_path, types)
    path = tmp_path / "corpus.jsonl"
    written = write_recording(path, pin_events(events), header={"route": "demo:effect"})

    recording = read_recording(path)
    replay = ReplayPins.from_events(
        recording.events, types, upcasts=UpcastRegistry(), on_miss="strict"
    )
    replayed = [
        replay.resolve(
            pin_key("demo", {"n": n}),
            kind="demo",
            effect=lambda: pytest.fail("drew"),
            interpret=int,
        )
        for n in (1, 2)
    ]

    assert written == 2, "only the pins -- not the rest of the log"
    assert replayed == [10, 20]
    assert draws == [1, 2], "each drawn once, during the recording, and never again"
    assert recording.header == {"route": "demo:effect"}


def test_events_come_back_exactly_as_stored(tmp_path: Path, types: EventTypeRegistry):
    """Same serialization as the log: every field, every version, unchanged."""
    events, _ = _drawn(tmp_path, types)
    path = tmp_path / "all.jsonl"
    write_recording(path, events, header={})

    assert list(read_recording(path).events) == events


def test_pin_events_drops_what_the_run_did(tmp_path: Path, types: EventTypeRegistry):
    """The corpus is the draws. What the run *did* cannot replay into a fresh store."""
    events, _ = _drawn(tmp_path, types)

    kept = list(pin_events(events))

    assert {e.envelope.type for e in events} == {"PinPromoted", "Noted"}
    assert {e.envelope.type for e in kept} == {"PinPromoted"}
    assert len(kept) == 2


def test_equal_events_write_the_same_bytes_whatever_order_their_keys_arrived_in(
    tmp_path: Path, types: EventTypeRegistry
):
    """
    So a re-recording's diff shows what changed in the run, not in how a dict was built.

    A pinned result is whatever the draw returned -- a provider's response, with its
    keys in whatever order that provider's client produced them. Two equal results built
    in a different order are the same draw. An earlier version of this test wrote the
    same events twice and compared, which passes without sorting at all, because one
    writer's order is always its own; it constrained nothing.
    """
    events, _ = _drawn(tmp_path, types)
    pin = next(pin_events(events))
    reordered = pin.model_copy(update={"payload": dict(reversed(list(pin.payload.items())))})
    assert reordered == pin and list(reordered.payload) != list(pin.payload)

    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_recording(first, [pin], header={"route": "x", "rerecord": "y"})
    write_recording(second, [reordered], header={"rerecord": "y", "route": "x"})

    assert first.read_bytes() == second.read_bytes()


# -- surviving a schema change --------------------------------------------------------------


class RecordedV1(DomainEvent, frozen=True):
    """An application's own promoted pin, as it was when the recording was made."""

    k: str
    r: JsonValue


class RecordedV2(DomainEvent, frozen=True):
    """The same event after a rename. The recording on disk still holds version 1."""

    key: str
    result: JsonValue


def test_a_recording_outlives_the_schema_it_was_written_under(tmp_path: Path):
    """
    The reason the format is the log's own rather than a test-facing one.

    Recorded under version 1; the application then renames the fields and ships version
    2 with an upcaster. The file is never touched, and it still replays, because a
    stored event is upcast on read wherever it is read from. A test-only format would
    have needed a migration of its own, and would have got one only after the first
    fixture silently stopped loading.

    The ``extract`` below reads ``.key`` and ``.result``, which exist only on version 2,
    so this passes only if the version-1 events were upcast on the way in.
    """
    then = EventTypeRegistry()
    then.register(EventKey("app", "Recorded", 1), RecordedV1)
    store = SQLiteEventStore(tmp_path / "then.db")
    key = pin_key("app", {"q": "what was settled"})
    store.append([then.pending(RecordedV1(k=key, r="sixty-forty"))])
    path = tmp_path / "then.jsonl"
    write_recording(path, store.read(), header={"route": "app:archive"})
    store.close()

    now = EventTypeRegistry()
    now.register(EventKey("app", "Recorded", 1), RecordedV1)
    now.register(EventKey("app", "Recorded", 2), RecordedV2)
    upcasts = UpcastRegistry()
    upcasts.register("app", "Recorded", 1, lambda p: {"key": p["k"], "result": p["r"]})

    def extract(_: StoredEvent, decoded: DomainEvent) -> tuple[str, JsonValue]:
        assert isinstance(decoded, RecordedV2), "read back un-upcast"
        return decoded.key, decoded.result

    recording = read_recording(path)
    assert recording.events[0].envelope.schema_version == 1, "the file was not rewritten"
    replay = ReplayPins.from_events(recording.events, now, extract=extract, upcasts=upcasts)

    assert replay.resolve(key, kind="app", effect=lambda: pytest.fail("drew"), interpret=str) == (
        "sixty-forty"
    )


# -- refusing what is not a recording -------------------------------------------------------


def test_a_file_without_the_header_is_refused(tmp_path: Path, types: EventTypeRegistry):
    """
    Events with no header line are not a recording.

    They could be loaded anyway, and then the one thing a pin corpus most needs to say --
    which route recorded it -- would be missing without anyone noticing.
    """
    events, _ = _drawn(tmp_path, types)
    path = tmp_path / "headless.jsonl"
    path.write_text("\n".join(e.model_dump_json() for e in events) + "\n")

    with pytest.raises(RecordingError, match="no 'anagrapheus.recording' header"):
        read_recording(path)


@pytest.mark.parametrize("marker", ["2", "true", "1.0"])
def test_an_unknown_format_version_is_refused(tmp_path: Path, marker: str):
    """``true`` and ``1.0`` compare equal to ``1`` in Python, and are not format 1."""
    path = tmp_path / "future.jsonl"
    path.write_text(f'{{"anagrapheus.recording":{marker}}}\n')
    with pytest.raises(RecordingError, match="recording format"):
        read_recording(path)


def test_a_header_cannot_claim_the_format_marker(tmp_path: Path):
    with pytest.raises(RecordingError, match="format marker"):
        write_recording(tmp_path / "x.jsonl", [], header={"anagrapheus.recording": 9})
