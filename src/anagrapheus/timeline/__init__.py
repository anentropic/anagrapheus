"""
The event-sourced timeline: the agent's authoritative record.

One append-only stream is what happened. Everything else — memory, state, indexes — is
a projection that can be dropped and rebuilt. Nondeterminism is pinned by key, so a
run replays or says exactly where it diverged.
"""

from anagrapheus.timeline._canonical import Json, canonical_json, sha256_hex
from anagrapheus.timeline.envelope import DomainEvent, Envelope, PendingEvent, StoredEvent
from anagrapheus.timeline.errors import (
    ConcurrencyError,
    DuplicateEventType,
    TimelineError,
    UnknownEventType,
    UpcastGap,
)
from anagrapheus.timeline.folds import (
    AsyncGenerativeFold,
    FoldDeterminism,
    GenerativeFold,
    GenerativeRebuild,
    OwnsPosition,
    ProjectionFold,
    Projector,
)
from anagrapheus.timeline.migrations import SchemaTooNew
from anagrapheus.timeline.pins import (
    DiscardedPin,
    PinLedger,
    PinPromoted,
    PinResolver,
    PinsAbandoned,
    ReplayDiverged,
    is_bookkeeping,
    pin_key,
    register_builtin_events,
)
from anagrapheus.timeline.recording import (
    Recording,
    RecordingError,
    pin_events,
    read_recording,
    write_recording,
)
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey, EventTypeRegistry
from anagrapheus.timeline.replay import Divergence, PinMiss, ReplayPins
from anagrapheus.timeline.sqlite import SQLiteEventStore
from anagrapheus.timeline.store import (
    EventStore,
    LedgerStore,
    PinScratchStore,
    ScratchRow,
    TransactionalStore,
)
from anagrapheus.timeline.upcast import EventReader, Upcast, UpcastRegistry

__all__ = [
    "RESERVED_NAMESPACE",
    "AsyncGenerativeFold",
    "ConcurrencyError",
    "DiscardedPin",
    "Divergence",
    "DomainEvent",
    "DuplicateEventType",
    "Envelope",
    "EventKey",
    "EventReader",
    "EventStore",
    "EventTypeRegistry",
    "Json",
    "LedgerStore",
    "PendingEvent",
    "PinLedger",
    "PinMiss",
    "PinPromoted",
    "PinResolver",
    "PinScratchStore",
    "PinsAbandoned",
    "FoldDeterminism",
    "GenerativeFold",
    "GenerativeRebuild",
    "OwnsPosition",
    "ProjectionFold",
    "Projector",
    "Recording",
    "RecordingError",
    "ReplayDiverged",
    "ReplayPins",
    "SQLiteEventStore",
    "SchemaTooNew",
    "ScratchRow",
    "StoredEvent",
    "TimelineError",
    "TransactionalStore",
    "UnknownEventType",
    "Upcast",
    "UpcastGap",
    "UpcastRegistry",
    "canonical_json",
    "is_bookkeeping",
    "pin_events",
    "pin_key",
    "read_recording",
    "register_builtin_events",
    "sha256_hex",
    "write_recording",
]
