---
title: How to Evolve Event Schemas with Upcasters
description: Change an event's payload shape without rewriting the log, so logs and recordings written by older code still read and fold
---

# How to Evolve Event Schemas with Upcasters

This guide shows how to change the shape of one of your events, for example adding a required field, while every log and recording written with the old shape still reads, folds and replays. Stored events are never rewritten; old payloads are brought up to date as they are read.

**Before you start**

- An application with its own registered event types, as in [How to Record a Run and Replay It Offline](record-and-replay.md).
- The vocabulary used below:
    - each event type is registered under a **schema version**, carried in the event's **envelope** (the metadata the library wraps around your payload);
    - an **upcast** is a function that rewrites a payload from one schema version to the next, applied when the event is read, never to the stored event;
    - a **fold** reads the log in order to build state, so every fold must see payloads in the current shape.

    [The Event Log and Projections](../explanation/event-log-and-projections.md) explains each of these.

## Who decides what

| The application decides | The library does |
|---|---|
| When a change needs a new schema version | Stamping each event with the version it was written under |
| How to fill in what old payloads lack | Chaining upcasts from the stored version to the latest |
| Which models are registered, and at which versions | Refusing to decode a payload it cannot bring up to date |

## Decide whether you need a new version

Not every change needs one. Prefer, in order:

1. **An optional field with a default.** Old payloads decode as they are, with no upcast and no version bump.
2. **A new event type**, for a genuinely new kind of fact.
3. **A version bump with an upcast**, when the meaning or required shape of an existing event changes. The rest of this guide covers this case.

## 1. Register the new version, and only the new version

Last release wrote `OfferMade` at version 1. This release needs every offer to name its stock and the size of the cut, so it registers a new model at version 2:

```python title="upcast.py"
import tempfile
from pathlib import Path

from pydantic import JsonValue

from anagrapheus import register_builtin_events
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    SQLiteEventStore,
    UpcastRegistry,
)

path = Path(tempfile.mkdtemp()) / "talks.db"


# --- last release: OfferMade v1 -------------------------------------------
class OfferMadeV1(DomainEvent, frozen=True):
    party: str
    text: str


old_types = EventTypeRegistry()
register_builtin_events(old_types)
old_types.event("fisheries", "OfferMade", 1)(OfferMadeV1)

store = SQLiteEventStore(path)
store.append([old_types.pending(OfferMadeV1(party="UK", text="A 12% cut."))])
store.close()


# --- this release: OfferMade v2 names the stock and the size of the cut ----
class OfferMade(DomainEvent, frozen=True):
    party: str
    stock: str
    cut_percent: int | None
    text: str


types = EventTypeRegistry()
register_builtin_events(types)
types.event("fisheries", "OfferMade", 2)(OfferMade)
```

The first half stands in for last release's code, writing a version 1 event to a file. In your application the old model is simply gone: register only the latest model for each type. New events are then always written at the latest version, and every reader decodes into one model.

## 2. Register an upcast for each step

An upcast takes the payload as a plain dict at one version and returns it at the next. Register one per step in an [`UpcastRegistry`][upcast-registry]:

```python title="upcast.py (continued)"
upcasts = UpcastRegistry()


@upcasts.upcast("fisheries", "OfferMade", 1)
def offer_v1_to_v2(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # Every v1 offer in the log was about mackerel; the cut was only in the prose.
    return {**payload, "stock": "mackerel", "cut_percent": None}
```

The number in [`upcast(...)`][upcast-decorator] is the version the step starts **from**. Version 3 later on means adding a step from 2, and a version 1 event then passes through both steps on its way in.

Upcasts must be pure functions of the payload. They run on every read, including every rebuild and every replay, years after the event was written. Fill gaps with values that were true when the old events were written, or with `None` where nothing is known, and never with anything computed from the current state.

## 3. Read through an `EventReader` everywhere

An [`EventReader`][event-reader] applies the upcasts as it reads. Use it for every read of the log, and give the same reader to every [`Projector`][projector], so folds always see the current shape:

```python title="upcast.py (continued)"
store = SQLiteEventStore(path)
store.append(
    [
        types.pending(
            OfferMade(party="Norway", stock="mackerel", cut_percent=5, text="5%.")
        )
    ]
)
reader = EventReader(store, upcasts, types)
for stored, offer in reader.read():
    print(stored.seq, stored.envelope.schema_version, offer)
print([e.envelope.schema_version for e in store.read()])
```

```text
1 2 party='UK' stock='mackerel' cut_percent=None text='A 12% cut.'
2 2 party='Norway' stock='mackerel' cut_percent=5 text='5%.'
[1, 2]
```

The reader returns both events at version 2. The store itself still holds the first one at version 1: nothing was rewritten.

Two places take raw events rather than a reader, and need the upcasts passed explicitly:

- **Recordings.** [`read_recording`][read-recording] returns events as stored, so pass `upcasts=` when replaying one: [`ReplayPins.from_events(recording.events, types, upcasts=upcasts)`][replay-from-events].
- **Your own code** that calls `store.read()` directly. Switch it to `reader.read()` (decoded) or `reader.read_raw()` (upcast, not decoded).

## Verify it worked

Keep a log or a recording made by the previous release as a fixture, and check in a test that it still reads and folds with the current code:

- `list(reader.read())` decodes every event without raising;
- a projector over a fresh projection catches up to `store.last_seq()`;
- a recorded run still replays with `on_miss="strict"`.

## Troubleshooting

### `UpcastGap: no upcast from v1 for ('fisheries', 'OfferMade'); latest registered schema is v2`

A stored event is at a version with no upcast step from it toward the latest. Register the missing step, or check that this reader was given the `UpcastRegistry` that holds it. The library raises rather than decode an old payload as if it were the new shape.

### `DuplicateEventType: event key already registered: EventKey(namespace='fisheries', type='OfferMade', schema_version=2)`

The same `(namespace, type, version)` was registered twice, or one model class was registered under two keys. Register each version once, at application start-up. An upcast step registered twice raises the same error.

### `ValueError: namespace 'anagrapheus' is reserved for library events`

Your events cannot use the library's namespace. Pick your own, such as `"fisheries"`.

### `SchemaTooNew: timeline schema is v99; this library knows up to v1`

This is about the database file, not your events. The SQLite file was last opened by a newer version of anagrapheus, which upgraded its tables, and older versions cannot open it. Upgrade the library. Opening a file with a newer library upgrades it automatically; there is no downgrade.

## Related

- [How to Record a Run and Replay It Offline](record-and-replay.md): replaying recordings made before a schema change.
- [The Event Log and Projections](../explanation/event-log-and-projections.md): envelopes, versions and why the log is never rewritten.

[upcast-registry]: ../reference/anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.UpcastRegistry
[event-reader]: ../reference/anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.EventReader
[upcast-decorator]: ../reference/anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.UpcastRegistry.upcast
[projector]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector
[read-recording]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.read_recording
[replay-from-events]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins.from_events
