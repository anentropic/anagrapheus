---
title: The Event Log and Projections
description: What envelopes, folds, projections and positions are in anagrapheus, and why every read model can be dropped and rebuilt from the log
---

# The Event Log and Projections

Everything in anagrapheus rests on an append-only event log and on projections folded from it. This page explains the vocabulary the rest of the documentation uses (envelope, fold, projection, position, upcast) and the guarantees that come with it.

## The log is what happened

The log is a single, globally ordered stream of events. Events are only ever appended: nothing in the library updates or deletes one. When the store accepts a batch it assigns each event a **`seq`**, a sequence number one higher than the last, and writes the batch atomically before returning. That number is an event's position in history, and every other component refers back to it.

An event has two halves:

- the **payload**, your own pydantic model subclassing [`DomainEvent`][domain-event], which carries the domain content (a party stated a position on a clause, a round closed);
- the **envelope**, the metadata wrapper the library puts around it.

Here is a `PositionStated` event from a negotiation, as the store returns it:

```python title="envelope.py"
import json

from anagrapheus.timeline import DomainEvent, EventTypeRegistry, SQLiteEventStore

types = EventTypeRegistry()


@types.event("negotiation", "PositionStated", version=1)
class PositionStated(DomainEvent, frozen=True):
    party: str
    clause: str
    position: str


store = SQLiteEventStore(":memory:")
pending = types.pending(PositionStated(party="uk", clause="fisheries", position="hold_firm"))
store.append([pending])

stored = next(store.read())
print(json.dumps(stored.model_dump(mode="json"), indent=2))
store.close()
```

```json
{
  "seq": 1,
  "envelope": {
    "event_id": "01a13299-53e2-733b-ac71-2989151b18bc",
    "namespace": "negotiation",
    "type": "PositionStated",
    "schema_version": 1,
    "causation_id": null,
    "correlation_id": null,
    "created_at": "2026-10-13T00:11:11.715008Z"
  },
  "payload": {
    "party": "uk",
    "clause": "fisheries",
    "position": "hold_firm"
  }
}
```

The envelope's fields are fixed, because they are the wire format the log will carry for as long as it exists:

- **`namespace`, `type` and `schema_version`** together name the payload's shape. The [`EventTypeRegistry`][registry] maps that triple to a model and back, and it is the only route between raw payloads and typed events.
- **`event_id`** is a UUIDv7, unique to this event.
- **`causation_id`** points at the event that directly caused this one, and **`correlation_id`** groups every event of one wider activity. Together they let you reconstruct why something happened without baking that story into payloads.
- **`seq`** is not in the envelope at all. An event awaiting append is a [`PendingEvent`][pending-event]; an event read back is a [`StoredEvent`][stored-event] that also carries its `seq`. Keeping them as separate types means code cannot accidentally treat an unappended event as if it had a position.

### Vocabularies are declared in code

Your application owns its event names. You register each payload model once, at start-up, and registering the same key or model twice fails immediately. A schema mismatch therefore shows up as a crash when the process starts, not as a surprise halfway through reading an old log.

The library emits events of its own (promoted pins, identity decisions, belief changes) under the reserved namespace `"anagrapheus"`. [`EventTypeRegistry.register`][registry-register] refuses that namespace, so an application event can never collide with a library one. You register the library's events with one call to `anagrapheus.register_builtin_events(registry)`.

!!! warning "Register every library event, not just one subpackage's"
    A reader that meets an event whose key has no registered model raises [`UnknownEventType`][unknown-event-type]. If you use the pin ledger and the identity layer over one log but register only the timeline's events, the first identity decision the reader reaches will fail. The package-level `register_builtin_events` covers every subpackage at once, and registering events you do not use costs nothing.

## Folds and projections

A **fold** is a function applied to events one at a time, in log order, to build up some state. The state it builds is a **projection**: a read model derived from the log, such as "which entity does the name *United Kingdom* refer to?" or "what does the EU believe the UK's red line on fisheries is?".

In anagrapheus a fold is any object implementing the [`ProjectionFold`][projection-fold] protocol: a `name`, an `apply(event, txn)` method, and a declared `fold` grade, explained below. A [`Projector`][projector] drives a list of folds over the log.

### Position: how far a projection has read

Each fold has a **position**: the `seq` of the last event it has folded, stored in the event store under the fold's name. [`Projector.catch_up()`][projector-catch-up] reads each fold's position, folds everything above it in batches, and commits the new position after each batch. Started fresh, it replays the whole log. Started on an existing database, it replays only what each fold has not yet seen.

The position commit and the fold's writes need to move together. If a crash lands between them, either the projection is ahead of its position (and events would be folded twice) or behind it (and events would be skipped). anagrapheus closes that gap in two ways:

- When the store supports transactions, as [`SQLiteEventStore`][sqlite-store] does, the projector applies each batch and commits the position inside one transaction. A projection whose tables live in the same database file therefore commits atomically with its position.
- For state that lives outside that transaction, the fold contract requires `apply` to tolerate the last batch being applied again after a crash. The belief fold, which writes to doxastica, recognises its own earlier writes and reuses them for exactly this reason.

!!! info "Position, not watermark"
    Some event-sourcing writing calls this a watermark. anagrapheus says *position*, which is the usual term for a subscription's progress through a stream and avoids confusion with stream-processing watermarks, which track event time.

### Why projections are disposable

Since a projection is computed from the log, it holds nothing the log does not. [`Projector.rebuild(name, reset=...)`][projector-rebuild] sets a fold's position back to zero, calls your `reset` callback to empty its state, and replays the whole log. This is what makes changing a storage backend "stand up the new adapter and replay", and why a bug in a fold is fixed by fixing the fold and rebuilding rather than by migrating its data.

The `reset_in_transaction=True` flag tells the projector that the projection's state lives in the store's own database, so the reset and the zeroed position can commit together. The default resets in a separate step, which is the safe choice for state elsewhere: a crash then leaves a replay over stale state, which `apply` must tolerate anyway, rather than an empty projection under a high position that catch-up would skip straight past.

### Scheduling is yours, not the fold's

`catch_up()` runs every fold the projector holds. Some projections must be current before the next turn starts (beliefs, identity); others must not block it (a prose summariser that calls a model). anagrapheus expresses that difference by composition rather than by a flag on the fold: build two projectors over two fold lists and call each on its own schedule. Each fold keeps an independent position, so the two never interfere.

## Deterministic and generative folds

Fold replay, the promise that the same log produces the same projection, holds only when `apply` is a pure function of the event and the state so far. Most folds are like that. A fold whose `apply` calls a model is not: the same log yields *a* projection, not *the* projection.

So every fold declares its grade through its [`fold`][fold-determinism] property, typed as `FoldDeterminism`:

- **`"deterministic"`**: `apply` is pure, and rebuilding reproduces the projection exactly. The identity and belief folds are deterministic.
- **`"generative"`**: `apply` depends on a model's output. What replays exactly is the structure the fold derives from the log (for the chronicle, which ranges were consolidated); the generated content is reproduced only where the model call's pin still hits.

The grade is required, with no default. A default would have to be `"deterministic"`, and a generative fold that forgot to declare itself would silently inherit a guarantee it cannot keep.

The projector treats the two grades differently in two places:

- **A generative fold must also implement [`GenerativeFold`][generative-fold]'s `prepare(event)`.** The projector calls it for every event in a batch *before* opening the write transaction, so the slow model call never holds SQLite's write lock. `apply` then writes what was prepared, with nothing slow left in it.
- **`rebuild` refuses a generative fold** and raises [`GenerativeRebuild`][generative-rebuild] unless you pass `discard_generated=True`. Rebuilding one re-runs every model call it ever made and returns different content, so it is neither cheap nor exact. The [chronicle](chronicle.md#why-rebuilding-needs-acknowledgement) is the generative fold that ships with the library.

## Evolving event schemas: upcast on read

Stored events are never rewritten, so a payload written under schema version 1 stays version 1 forever. When your model changes, you register the new shape under version 2 and supply an **upcast**: a pure function that turns a version-1 payload dictionary into a version-2 one. Upcasts live in an [`UpcastRegistry`][upcast-registry] and chain, so a version-1 event passes through the 1→2 step and then the 2→3 step on its way to a reader expecting version 3.

The upcasts run as events are read, inside [`EventReader`][event-reader], which is what the projector and the assembly slices read through. Folds therefore only ever see the latest shape of each event type.

The reader is strict about gaps. If the chain stops short of the latest registered version, it raises [`UpcastGap`][upcast-gap] rather than decoding a stale payload into the new model and hoping the fields line up. The registry prefers additive change: add an optional field first, then a new event type, and bump a version only when neither will do. See [How to Evolve Event Schemas with Upcasters](../how-to/evolve-event-schemas.md) for the procedure.

??? info "Table schemas are versioned separately"
    Payload evolution never touches the database tables. The tables themselves (the event log, positions, pin scratch, and each projection's own tables) migrate automatically when a store opens its connection, with each subpackage tracking its own schema version. Downgrade is not supported: opening a file written by a newer version of the library raises [`SchemaTooNew`][schema-too-new] rather than risking a write the newer code would misread.

## Where to go next

- [Pinning and Replay](pinning-and-replay.md) explains the events the pin ledger appends and how replay reads them back.
- [Beliefs](beliefs.md) and [Identity](identity.md) are the two deterministic projections the library ships.
- [The Chronicle](chronicle.md) is the generative one.

[domain-event]: ../reference/anagrapheus/timeline/envelope.md#anagrapheus.timeline.envelope.DomainEvent
[pending-event]: ../reference/anagrapheus/timeline/envelope.md#anagrapheus.timeline.envelope.PendingEvent
[stored-event]: ../reference/anagrapheus/timeline/envelope.md#anagrapheus.timeline.envelope.StoredEvent
[registry]: ../reference/anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry
[registry-register]: ../reference/anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry.register
[projection-fold]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.ProjectionFold
[generative-fold]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.GenerativeFold
[projector]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector
[fold-determinism]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.ProjectionFold.fold
[upcast-registry]: ../reference/anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.UpcastRegistry
[event-reader]: ../reference/anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.EventReader
[unknown-event-type]: ../reference/anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.UnknownEventType
[projector-catch-up]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector.catch_up
[sqlite-store]: ../reference/anagrapheus/timeline/sqlite.md#anagrapheus.timeline.sqlite.SQLiteEventStore
[projector-rebuild]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector.rebuild
[generative-rebuild]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.GenerativeRebuild
[upcast-gap]: ../reference/anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.UpcastGap
[schema-too-new]: ../reference/anagrapheus/timeline/migrations.md#anagrapheus.timeline.migrations.SchemaTooNew
