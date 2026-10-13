"""
A recorded run on disk, in the log's own serialization.

A recorded pin ledger is a fixture, but it is also the log format: does it use the
same serialization, or a test-facing one? Until this module the question was open in
practice as well as on paper. Every replay in the repository was built from a live store
in the same test that recorded it, so the strongest form of the replay claim — a run
recorded earlier, by another process, loaded from a file — had never been exercised.

**Same serialization.** Every event line is a :class:`~anagrapheus.timeline.StoredEvent`
exactly as the store holds it. The reason is not tidiness: an event in the log's own
format is **upcast as it is read into a replay**, like any other —
:meth:`~anagrapheus.timeline.ReplayPins.from_events` with ``upcasts=`` — so a recording
made today survives a future change to the event it carries. A test-facing format would
need a migration story of its own, and would get one only after the first fixture had
silently stopped loading.

**One header line**, first, saying what produced the events. For a pin corpus that
matters more than it looks: a pin key includes the model's id, so the same story
recorded through a different provider route is a different corpus, and a replay against
the wrong one misses on every draw. The header is where a reader finds out which.

**Canonical JSON throughout**, sorted keys and no whitespace, so re-recording an
unchanged run changes only what genuinely differs — ids, timestamps, the responses — and
a recording's diff is reviewable rather than reshuffled.
"""

# Design: note 00 §8.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, NamedTuple, cast

from anagrapheus.timeline._canonical import canonical_json, concrete_json
from anagrapheus.timeline.envelope import StoredEvent
from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.registry import RESERVED_NAMESPACE

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

    from anagrapheus.timeline._canonical import Json

__all__ = [
    "RECORDING_FORMAT",
    "Recording",
    "RecordingError",
    "pin_events",
    "read_recording",
    "write_recording",
]

#: The header key that marks a file as a recording, and the format version it holds.
RECORDING_FORMAT = "anagrapheus.recording"
_VERSION = 1


class RecordingError(TimelineError):
    """A file that is not a recording this version of the library can read."""


class Recording(NamedTuple):
    """A recording read back: what produced it, and the events."""

    #: Whatever the writer said about the run — its route, how to re-record it.
    header: Mapping[str, Json]
    events: tuple[StoredEvent, ...]


def pin_events(events: Iterable[StoredEvent]) -> Iterator[StoredEvent]:
    """
    The pin corpus: only the events a replay reads.

    A whole log is mostly what the run *did* — minted beliefs, established entities —
    and none of it can be replayed into a fresh store, whose ids will all differ. What
    makes a run reproducible is the pins, so that is what a recording usually keeps.
    """
    for stored in events:
        if (stored.envelope.namespace, stored.envelope.type) == (RESERVED_NAMESPACE, "PinPromoted"):
            yield stored


def write_recording(
    path: Path, events: Iterable[StoredEvent], *, header: Mapping[str, Json]
) -> int:
    """
    Write ``events`` to ``path``, after one header line. Returns how many were written.

    The header must not claim the format key itself: that key is how a reader tells a
    recording from any other JSON-lines file, and letting a caller overwrite it would
    make a file that announces one format and holds another.
    """
    if RECORDING_FORMAT in header:
        raise RecordingError(f"{RECORDING_FORMAT!r} is the format marker, not header content")
    lines = [canonical_json(cast("Json", {RECORDING_FORMAT: _VERSION, **header}))]
    lines.extend(canonical_json(concrete_json(stored.model_dump(mode="json"))) for stored in events)
    # LF on every platform: text mode would write CRLF on Windows, and a re-recording
    # there would rewrite every line of the fixture.
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines) - 1


def read_recording(path: Path) -> Recording:
    """
    Read a recording back. Refuses anything that does not say it is one.

    Raises rather than guessing on a missing or unknown format marker. A JSON-lines file
    of events with no header could be loaded — and then the one thing a pin corpus most
    needs to say, which route recorded it, would be missing without anyone noticing.

    Events come back exactly as stored: **not upcast**. Pass them to
    :meth:`~anagrapheus.timeline.ReplayPins.from_events` with ``upcasts=`` so they are
    brought to the current schema on the way in, as anything read from a log is.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RecordingError(f"{path} is empty, so it is not a recording")
    parsed: Json = json.loads(lines[0])
    if not isinstance(parsed, dict) or RECORDING_FORMAT not in parsed:
        raise RecordingError(
            f"{path} has no {RECORDING_FORMAT!r} header on its first line, so it is not a "
            "recording -- or it is one whose header was lost, which would hide the route "
            "it was recorded through"
        )
    header = dict(parsed)
    version = header.pop(RECORDING_FORMAT)
    # `type(...) is int`, not `==`: JSON `true` and `1.0` both compare equal to 1.
    if type(version) is not int or version != _VERSION:
        raise RecordingError(
            f"{path} is recording format {version!r}; this version of the library reads "
            f"format {_VERSION}"
        )
    events = tuple(StoredEvent.model_validate(json.loads(line)) for line in lines[1:] if line)
    return Recording(header=header, events=events)
