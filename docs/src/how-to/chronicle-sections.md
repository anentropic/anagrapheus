---
title: How to Consolidate History into Chronicle Sections
description: Summarise each closed round of a long run into a section that cites the events it covers, and read recent events beside the summarised past
---

# How to Consolidate History into Chronicle Sections

This guide shows how to keep a long run's history inside a context window: each time a unit of work closes (a negotiating round, a scene), the events since the last one are summarised into a chronicle section, and an agent reads the summarised past next to the recent events verbatim.

**Before you start**

- A working pinned model, as in [How to Pin Model and Tool Calls](pin-model-and-tool-calls.md). Summaries are model calls, and they should be pinned like any other.
- The vocabulary used below:
    - a **section** is prose a model wrote over a range of the log, citing the range it covers;
    - **consolidation** is writing a section when a unit closes; it never deletes or edits the events it summarises;
    - **recency vs depth**: recent events are read verbatim from the log, and the older past is read as sections;
    - a section's **anchor** (its range, its unit and what it supersedes) is derived from the log and replays exactly, while its **prose** is generated and differs if regenerated.

    [The Chronicle](../explanation/chronicle.md) explains each of these.

## Who decides what

| The application decides | The library does |
|---|---|
| What closes a unit (the boundary event) | Detecting each boundary and the range since the last |
| The summariser: its prompt, its model, what it renders | Recording each section with its range and pins |
| When the consolidation projector runs | Keeping consolidation off the write lock |
| How to retrieve sections for a query | Separating replayable anchors from generated prose |

## 1. Write the summariser

A [`Summarizer`][summarizer] is any callable that takes the range's stored events and returns a [`Consolidation`][consolidation]: the prose, plus the pin keys its model calls resolved under. Draw through a [`PinnedModel`][pinned-model] and collect the keys with `on_key`:

```python title="chronicle.py"
from collections.abc import Sequence

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.chronicle import (
    Brief,
    ChronicleFold,
    Consolidation,
    EventTypes,
    SQLiteChronicleStore,
    substring_retriever,
)
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    PinLedger,
    Projector,
    SQLiteEventStore,
    StoredEvent,
    UpcastRegistry,
)


class OfferMade(DomainEvent, frozen=True):
    party: str
    text: str


class RoundClosed(DomainEvent, frozen=True):
    round_number: int


def summariser_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("The UK opened at 12%; Norway held at 5%.")])


class RoundSummary:
    """The application's summariser: renders a round, draws through a pinned model."""

    def __init__(self, resolver) -> None:
        self._resolver = resolver

    def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        lines = [
            f"{e.payload['party']}: {e.payload['text']}"
            for e in events
            if e.envelope.type == "OfferMade"
        ]
        keys: list[str] = []
        model = PinnedModel(FunctionModel(summariser_model), self._resolver, on_key=keys.append)
        result = Agent(model).run_sync(
            "Summarise this round of a fisheries negotiation in one sentence:\n" + "\n".join(lines)
        )
        return Consolidation(prose=result.output, pins=tuple(keys))
```

The summariser receives every event in the range, including library events such as committed pins. Render only your own domain content, and never sequence numbers, event ids or timestamps: those differ between runs, and putting them in the prompt makes every replayed summary a pin miss.

## 2. Install the fold on its own projector

A [`ChronicleFold`][chronicle-fold] watches for your boundary event. [`EventTypes`][event-types] names boundary types outright; implement [`UnitBoundary`][unit-boundary] for anything more involved.

```python title="chronicle.py (continued)"
types = EventTypeRegistry()
register_builtin_events(types)
types.event("fisheries", "OfferMade")(OfferMade)
types.event("fisheries", "RoundClosed")(RoundClosed)

store = SQLiteEventStore(":memory:")
reader = EventReader(store, UpcastRegistry(), types)
chronicle = SQLiteChronicleStore(store.connection)
ledger = PinLedger(store, types)
fold = ChronicleFold(
    chronicle,
    reader,
    boundary=EventTypes(("fisheries", "RoundClosed")),
    summarize=RoundSummary(ledger),
)
# A projector of its own, so consolidation runs off the turn's hot path.
consolidate = Projector(store, reader, [fold])
```

Each projector keeps an independent position per fold, so you can run belief and identity folds after every append and run `consolidate.catch_up()` on its own schedule, for example at the end of each round. From async code, await `consolidate.catch_up_async()` instead; see [Consolidate from async code](#consolidate-from-async-code). The chronicle fold is **generative**, meaning its `apply` makes a model call, and it makes that call before taking the database's write lock, so a slow summary never blocks appends.

## 3. Close units and consolidate

Append events as the run goes, including the boundary, then run the consolidation projector and commit the summariser's pins:

```python title="chronicle.py (continued)"
store.append(
    [
        types.pending(OfferMade(party="UK", text="A 12% cut to the mackerel quota.")),
        types.pending(OfferMade(party="Norway", text="No more than 5%.")),
        types.pending(RoundClosed(round_number=1)),
        types.pending(OfferMade(party="UK", text="We could move to 10%.")),
    ]
)
consolidate.catch_up()
ledger.commit()
```

Round 1 (events 1 to 3) becomes one section, with id `"1-3"`. The UK's new offer at seq 4 belongs to round 2, which is still open, so it is not consolidated yet.

## 4. Read recent events and the summarised past

A [`Brief`][brief] splits recency from depth. Read the consolidated past as sections, and everything after it verbatim from the log:

```python title="chronicle.py (continued)"
brief = Brief(chronicle, reader)
through = brief.anchors.consolidated_through()
print("consolidated through", through)
for section in brief.depth():
    print(section.anchor.section_id, section.prose, section.pins)
for event in brief.tail(since_seq=through).events:
    if event.envelope.namespace == "fisheries":
        print("recent:", event.seq, event.payload)
print([s.anchor.section_id for s in substring_retriever(brief.depth(), "norway")])
```

```text
consolidated through 3
1-3 The UK opened at 12%; Norway held at 5%. ('p1:model_call:1904ca75…',)
recent: 4 {'party': 'UK', 'text': 'We could move to 10%.'}
['1-3']
```

The tail always comes from the log, never from the chronicle. If consolidation falls behind, the tail simply starts earlier and nothing is missing.

There are two read surfaces, at two grades:

- `brief.anchors` ([`Anchors`][anchors]) has ranges, coverage and [`consolidated_through()`][consolidated-through], but no prose. Use it for anything that decides something: gating, validation, what to consolidate next.
- `brief.prose` ([`Prose`][prose]) and `brief.depth()` return sections with their prose. Prose is advisory: show it to a model, but never let it settle anything.

[`substring_retriever`][substring-retriever] is a deliberately naive case-insensitive search, most recent first. For real retrieval, write your own [`Retriever`][retriever]: any callable taking `(sections, query)` and returning sections.

`Brief` and `Brief.tail` are for reading the split yourself: inspecting, logging, testing. They are not slices, so nothing they return can go into an assembled prompt. A prompt reads the same split through two slices in one grant: a [`ChronicleSlice`][chronicle-slice] for the sections, labelled as generated, and a [`LogSlice`][log-slice] read from the end of the last section. [Put chronicle prose in a prompt](assemble-prompts.md#put-chronicle-prose-in-a-prompt) continues this script with that grant, budgeted and sent through a pinned model.

## Verify it worked

Check the anchors rather than the prose: `brief.anchors.all()` lists one anchor per closed unit, and `brief.anchors.covers(seq)` returns the anchor containing a given event. Each section's `pins` should be non-empty when the summariser drew through a ledger, which shows that the summary can be replayed.

## Carry the chronicle to a new store

Every other projection can be dropped and rebuilt from the log for free. The chronicle cannot: rebuilding it re-runs every summary call and, unless every pin still hits, returns different prose. When you move to a new database, carry the sections across with `export` and `load`:

```python
from pathlib import Path

new_store = SQLiteEventStore(Path("new-backend.db"))
carried = SQLiteChronicleStore(new_store.connection)
print(carried.load(chronicle.export()))  # sections written: 1
```

`load` skips section ids already present, so an interrupted transfer can be repeated. It is a transfer of one chronicle, not a merge of two: two chronicles over the same log share section ids, and `load` keeps what is already there.

## Rebuild only when you mean it

[`Projector.rebuild`][projector-rebuild] refuses a generative fold:

```text
GenerativeRebuild: 'chronicle' is a generative fold: rebuilding re-runs every model call it made and returns the same anchors with different content. Carry it across instead, or pass discard_generated=True to accept both.
```

!!! danger "Rebuilding discards every section's prose"
    `discard_generated=True` drops every section and regenerates it. Anything that read the old prose read something that no longer exists, and every summary is paid for again. The anchors come back identical; the prose comes back identical only where the summariser's pins still hit.

To rebuild with identical prose, give the summariser a [`ReplayPins`][replay-pins] over the log, so every summary is answered from its pin:

```python
from anagrapheus.chronicle import FOLD_NAME
from anagrapheus.timeline import ReplayPins

replay = ReplayPins.from_events(store.read(), types)
replayed_fold = ChronicleFold(
    chronicle,
    reader,
    boundary=EventTypes(("fisheries", "RoundClosed")),
    summarize=RoundSummary(replay),
)
Projector(store, reader, [replayed_fold]).rebuild(
    FOLD_NAME,
    reset=chronicle.reset,
    reset_in_transaction=True,
    discard_generated=True,
)
```

## Consolidate from async code

`consolidate.catch_up()` cannot run inside an event loop when the summariser calls `Agent.run_sync`, which refuses to start while a loop is already running. Moving the call into `asyncio.to_thread` does not help either, because a SQLite connection may only be used by the thread that opened it. Instead, write the summariser as a coroutine, an [`AsyncSummarizer`][async-summarizer], and drive the projector with [`catch_up_async()`][projector-catch-up-async]:

```python title="chronicle.py (continued)"
import asyncio


def round_two_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("The UK moved to 10%; Norway met it at 8%.")])


class AwaitedRoundSummary:
    """The same summariser, awaited: it draws with `Agent.run` rather than `run_sync`."""

    def __init__(self, resolver) -> None:
        self._resolver = resolver

    async def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        lines = [
            f"{e.payload['party']}: {e.payload['text']}"
            for e in events
            if e.envelope.type == "OfferMade"
        ]
        keys: list[str] = []
        model = PinnedModel(FunctionModel(round_two_model), self._resolver, on_key=keys.append)
        result = await Agent(model).run(
            "Summarise this round of a fisheries negotiation in one sentence:\n" + "\n".join(lines)
        )
        return Consolidation(prose=result.output, pins=tuple(keys))


async def close_round_two() -> None:
    store.append(
        [
            types.pending(OfferMade(party="Norway", text="We could go to 8%.")),
            types.pending(RoundClosed(round_number=2)),
        ]
    )
    ledger = PinLedger(store, types)
    fold = ChronicleFold(
        chronicle,
        reader,
        boundary=EventTypes(("fisheries", "RoundClosed")),
        summarize=AwaitedRoundSummary(ledger),
    )
    await Projector(store, reader, [fold]).catch_up_async()
    ledger.commit()


asyncio.run(close_round_two())
for section in brief.depth():
    print(section.anchor.section_id, section.prose)
```

```text
1-3 The UK opened at 12%; Norway held at 5%.
4-7 The UK moved to 10%; Norway met it at 8%.
```

Round 2 covers events 4 to 7: the UK offer that was still open in step 3, the pin that `ledger.commit()` promoted into the log, and the two events appended here.

Only the summaries are awaited. The projector awaits each one with no transaction open, then writes the batch and its position synchronously on the calling thread, so the store is only ever used from the thread that opened it. The pinned model resolves its pins through the ledger's awaited path on that same thread.

Some things to know about the awaited path:

- **It takes synchronous folds too.** Belief and identity folds, and a generative fold with only a synchronous `prepare`, run under `catch_up_async()` as they do under `catch_up()`, so one projector can hold both kinds.
- **`catch_up()` refuses an async summariser.** It raises `TypeError` without calling the model, because it has no way to await the summary.
- **Rebuild with [`rebuild_async()`][projector-rebuild-async].** It takes the same arguments as `rebuild()` and refuses a generative fold in the same way.
- **Each fold's position is unchanged by a failed batch.** If a summary raises, nothing from that batch is written, and the next `catch_up_async()` starts the batch again.
- **Do not run one projector from two tasks at once.** Each would fold the same batch.

To write a generative fold of your own that awaits its draws, implement [`AsyncGenerativeFold`][async-generative-fold]'s `prepare_async(event)` beside `apply`.

## Troubleshooting

### No sections appear

The boundary never matched. `EventTypes` compares `(namespace, type)` exactly, so check both against your registration. Also check that something calls `catch_up()` on the projector holding the chronicle fold: a projector holding only your deterministic folds will never consolidate.

### A replayed summary misses its pin

The summary prompt changed between recording and replay. The usual cause is rendering something that differs per run, such as `seq`, `event_id` or `created_at`. Render domain payload fields only.

### `RuntimeError: This event loop is already running` from async code

The summariser calls `Agent.run_sync` and something called `catch_up()` from inside an event loop. Make the summariser a coroutine that calls `Agent.run`, and await `catch_up_async()` instead, as in [Consolidate from async code](#consolidate-from-async-code).

### `TypeError: 'chronicle' has an async summarizer`

The fold's summariser is a coroutine, and its projector was driven with `catch_up()` or `rebuild()`. Use `catch_up_async()` or `rebuild_async()`.

### The tail contains `PinPromoted` events

[`Brief.tail`][brief-tail] returns every event above the position, library events included. Filter on `event.envelope.namespace`, as in step 4.

## Related

- [How to Assemble Prompts from Granted Slices](assemble-prompts.md#put-chronicle-prose-in-a-prompt): putting sections and recent events into one prompt, with sections labelled as generated.
- [How to Pin Model and Tool Calls](pin-model-and-tool-calls.md): the pinned model the summariser draws through.
- [The Chronicle](../explanation/chronicle.md): why sections cite their range, and why the chronicle is carried rather than rebuilt.

[summarizer]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.Summarizer
[async-summarizer]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.AsyncSummarizer
[consolidation]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.Consolidation
[chronicle-fold]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.ChronicleFold
[event-types]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.EventTypes
[unit-boundary]: ../reference/anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.UnitBoundary
[brief]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Brief
[anchors]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Anchors
[prose]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Prose
[retriever]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Retriever
[substring-retriever]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.substring_retriever
[pinned-model]: ../reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[consolidated-through]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Anchors.consolidated_through
[chronicle-slice]: ../reference/anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.ChronicleSlice
[log-slice]: ../reference/anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.LogSlice
[projector-rebuild]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector.rebuild
[replay-pins]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[projector-catch-up-async]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector.catch_up_async
[projector-rebuild-async]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector.rebuild_async
[async-generative-fold]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.AsyncGenerativeFold
[brief-tail]: ../reference/anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Brief.tail
