"""
The log's hash chain: each event's hash covers its predecessor's.

Every stored event carries a SHA-256 ``hash`` over its envelope, its raw payload and the
hash of the event before it, so editing, removing or reordering any event in place
breaks the chain from that point on, and :func:`verify_chain` reports where. The store
computes the hash on append; nothing else writes one.

**What it is not.** It is tamper-*evident*, not a signature: anyone who can write the
file can rewrite the whole chain from the edit onward. Nor can the chain alone notice
events cut off the end, since a shorter log is still a valid chain. Both are answered
the same way, by keeping a hash somewhere else: a later log that records the hash it
was copied from, a fixture's checked-in head, a backup. :func:`verify_chain` returns the
head so there is one to keep.

**What it covers.** The envelope's fields and the payload *as stored*, before any
upcast, so a schema upgrade that only changes how events are read never touches the
chain. ``seq`` is not covered: an event's position is already fixed by its
predecessor's hash, and leaving it out means two logs that hold the same events in the
same order (a copy, a fork's prefix) hold the same hashes.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from anagrapheus.timeline._canonical import exact_json
from anagrapheus.timeline.errors import TimelineError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from anagrapheus.timeline.envelope import Envelope, StoredEvent

__all__ = ["CHAIN_SCHEME", "ChainBroken", "event_hash", "verify_chain"]

#: The hash scheme, mixed into every hash. Changing what a hash covers bumps it, so
#: an old chain fails loudly instead of matching by accident.
CHAIN_SCHEME: Final = "h1"


class ChainBroken(TimelineError):
    """
    A stored hash does not match the one its event and predecessor give.

    ``seq`` is the first event whose hash is wrong. That event, or one before it, was
    changed, removed or moved after it was written.
    """

    def __init__(self, seq: int, expected: str, found: str | None) -> None:
        self.seq = seq
        self.expected = expected
        self.found = found
        super().__init__(f"hash chain broken at seq {seq}: expected {expected}, found {found}")


def event_hash(prev: str | None, envelope: Envelope, payload: Mapping[str, JsonValue]) -> str:
    """
    Return the hash of one event, given its predecessor's hash (``None`` for the first).

    The material is the scheme, ``prev``, every envelope field and the payload,
    serialized as JSON with sorted keys and no whitespace. Strings are hashed exactly
    as given, not normalized as pin keys are: this hash is for spotting a changed
    byte, where a pin key is for matching equivalent requests. A store other than
    SQLite computes the same function, so a log verifies the same wherever it lives.
    """
    material = {
        "scheme": CHAIN_SCHEME,
        "prev": prev,
        "event_id": str(envelope.event_id),
        "namespace": envelope.namespace,
        "type": envelope.type,
        "schema_version": envelope.schema_version,
        "causation_id": str(envelope.causation_id) if envelope.causation_id else None,
        "correlation_id": str(envelope.correlation_id) if envelope.correlation_id else None,
        "created_at": envelope.created_at.isoformat(),
        "payload": payload,
    }
    return hashlib.sha256(exact_json(material).encode("utf-8")).hexdigest()


def verify_chain(events: Iterable[StoredEvent], *, prev: str | None = None) -> str | None:
    """
    Check every event's hash in order; return the last one, the log's head.

    Pass events as the store reads them, or as a recording holds them: raw, not upcast.
    An upcast event carries no hash, since its payload is no longer the one stored, so
    it fails here rather than verifying against content it does not have.

    ``prev`` is the hash of the event before the first one passed, for checking a log
    from part-way through; leave it out to check from the start. Returns ``prev``
    unchanged when there are no events, so an empty log's head is ``None``.

    Raises :class:`ChainBroken` at the first event whose stored hash is wrong.
    """
    head = prev
    for event in events:
        expected = event_hash(head, event.envelope, event.payload)
        if event.hash != expected:
            raise ChainBroken(event.seq, expected, event.hash)
        head = expected
    return head
