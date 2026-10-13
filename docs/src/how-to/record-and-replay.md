---
title: How to Record a Run and Replay It Offline
description: Record an agent run's model calls to a file and replay it later with no model reached, so a bug can be reproduced and a test needs no network
---

# How to Record a Run and Replay It Offline

This guide shows how to record the model calls of an agent run into a recording file, then replay that run in another process without calling the model once. Use it to turn a run that misbehaved into a reproducible fixture, or to give a test suite recorded model answers instead of a live provider.

**Before you start**

- `anagrapheus` installed, with your pydantic-ai agent code at hand. The examples use pydantic-ai's `FunctionModel`, so they run offline.
- A rough idea of what pinning is. In short: a **pin** records the result of a model call under a **key** built from what the model was asked. **Replay** answers each call from the recorded pins and never reaches the model; **re-running** calls the model again and gets a fresh answer. [Pinning and Replay](../explanation/pinning-and-replay.md) explains the idea in full.

## Who decides what

| The application decides | The library does |
|---|---|
| Which events its domain has, and registering them | Registering its own events in one call |
| What a unit of work is, and when to commit it | Keying every model call and recording the draw |
| What goes in the recording header, and where the file lives | Writing and reading the recording format |
| Whether a replay miss should stop the run or continue it | Detecting the miss and saying where it happened |

## 1. Register every event type

An `EventTypeRegistry` maps each event type to its pydantic model. The log holds the library's own events (pins, identity decisions, beliefs) next to yours, so register both: `anagrapheus.register_builtin_events` covers every library event in one call.

```python title="record_replay.py"
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import (
    DomainEvent,
    EventStore,
    EventTypeRegistry,
    PinLedger,
    PinResolver,
    ReplayPins,
    SQLiteEventStore,
    pin_events,
    read_recording,
    write_recording,
)


class OfferMade(DomainEvent, frozen=True):
    party: str
    text: str


def build_types() -> EventTypeRegistry:
    types = EventTypeRegistry()
    register_builtin_events(types)  # every library event, in one call
    types.event("fisheries", "OfferMade")(OfferMade)  # then your own
    return types
```

Build the registry the same way in the process that records and in the process that replays. A single function such as `build_types()` makes that hard to get wrong.

## 2. Write the unit of work against a resolver

A **resolver** is the object a pinned call draws through. The live one is a [`PinLedger`][pin-ledger], which calls the model and records the answer. The replaying one is [`ReplayPins`][replay-pins], which answers from a recording. Both implement the same interface, so write the unit of work once and pass the resolver in:

```python title="record_replay.py (continued)"
model_calls = 0


def uk_negotiator(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    global model_calls
    model_calls += 1
    return ModelResponse(parts=[TextPart("We offer a 12% cut to the mackerel quota.")])


def opening_round(
    store: EventStore, types: EventTypeRegistry, model: Model, resolver: PinResolver
) -> str:
    agent = Agent(PinnedModel(model, resolver))
    result = agent.run_sync("UK delegation: make your opening offer on mackerel.")
    store.append([types.pending(OfferMade(party="UK", text=result.output))])
    return result.output
```

[`PinnedModel`][pinned-model] wraps any pydantic-ai model, so the agent itself does not change. The counter in `uk_negotiator` is there only to show, later on, that replay never reaches the model.

## 3. Record the run

Run the unit of work through a `PinLedger`, commit it, and write the pins to a file:

```python title="record_replay.py (continued)"
recording_path = Path("opening.pins.jsonl")
types = build_types()
model = FunctionModel(uk_negotiator)

store = SQLiteEventStore(":memory:")
ledger = PinLedger(store, types)
recorded = opening_round(store, types, model, ledger)
ledger.commit()

count = write_recording(
    recording_path,
    pin_events(store.read()),
    header={"route": model.model_id, "rerecord": "python record_replay.py"},
)
store.close()
print(f"recorded {count} pin(s) from {model_calls} model call(s)")
# recorded 1 pin(s) from 1 model call(s)
```

What each call does:

- [`PinLedger.commit()`][pin-ledger-commit] promotes the unit's pins into the log as ordinary `PinPromoted` events. Until then they sit in a scratch area that a reader of the log never sees.
- [`pin_events`][pin-events] keeps only the pin events. The rest of the log (your `OfferMade`, entity and belief events) is what the run *did*, and replaying the run produces those again.
- [`write_recording`][write-recording] writes one header line and then one event per line, as canonical JSON, so re-recording an unchanged run gives a small, reviewable diff.

!!! tip "Say in the header which model recorded the run"
    A pin key includes the model's id. The same run recorded through a different provider or model is a different set of pins, and replaying against the wrong one misses on every call. The header is free-form JSON, so record the route and how to re-record.

## 4. Replay the run offline

In another process (here, further down the same script), read the file back, build a `ReplayPins` from it, and run the same unit of work against a fresh store:

```python title="record_replay.py (continued)"
recording = read_recording(recording_path)
print("recorded through:", recording.header["route"])

types = build_types()
replay = ReplayPins.from_events(recording.events, types, on_miss="strict")
fresh = SQLiteEventStore(":memory:")

calls_before = model_calls
replayed = opening_round(fresh, types, model, replay)
print("replayed:", replayed)
print("model calls during replay:", model_calls - calls_before)
```

[`ReplayPins.from_events`][replay-from-events] takes any iterable of stored events: a recording, or the live log read with `store.read()`. With `on_miss="strict"`, a call that has no recorded pin raises instead of reaching the model.

## Verify it worked

Running the whole script prints:

```text
recorded 1 pin(s) from 1 model call(s)
recorded through: function:function:uk_negotiator:
replayed: We offer a 12% cut to the mackerel quota.
model calls during replay: 0
```

Zero model calls during replay is the property to test for. Under strict replay the model is never awaited, so a test can assert it rather than hope for it. `replay.divergence` is `None` when every call was answered from the recording.

## Use the recording with a real provider

Recording works the same against a real model. Pass a model string or a configured pydantic-ai model to `PinnedModel`, with the `anthropic` or `openai` extra installed and the provider's API key set:

```python
agent = Agent(PinnedModel("anthropic:claude-sonnet-4-5", ledger))
```

Record once against the provider, commit the recording file to your repository, and replay it in tests with `ReplayPins`. Replay never sends a request, but pydantic-ai still needs a credential to construct the provider's model object, so set a placeholder such as `ANTHROPIC_API_KEY=unused` where tests replay. Record the model id in the header, since it is part of every key.

## Continue past a miss instead of stopping

Strict replay suits tests. When exploring ("what happens if the UK opens differently?"), use `on_miss="record"`: the first miss is recorded as a [`Divergence`][divergence], the call goes to the model, and the run continues as a new run. Pass `announce` to write that divergence into the log as a `ReplayDiverged` event:

```python
replay = ReplayPins.from_events(
    store.read(),
    types,
    on_miss="record",
    announce=ReplayPins.announce_to(store, types, unit_id=ledger.unit_id),
)
```

Fresh draws made in record mode are held by the `ReplayPins` object for reuse within that run. They are not written to the log. To keep them, run the new branch through a `PinLedger` and commit it.

## Troubleshooting

### `UnknownEventType: no versions registered for ('anagrapheus', 'established')`

The registry is missing some library events, here the identity layer's. Register them all with `anagrapheus.register_builtin_events(types)`. The function of the same name in `anagrapheus.timeline` covers only the pin events.

### `PinMiss: replay diverged at key 'p1:model_call:…' (model_call) after 0 hit(s)`

The replaying code asked the model something the recording has no answer for. Typical causes:

- the prompt, the instructions or a tool definition changed since recording;
- a different model, or a `FunctionModel` wrapping a function with a different name (the function name is part of the model id);
- a model setting that changes the draw, such as temperature, is different.

The exception's `divergence` attribute says which key missed and after how many hits. If the change was intended, re-record. The miss is the library refusing to present a fresh answer as a replay.

### `RecordingError: … has no 'anagrapheus.recording' header on its first line`

The file is not a recording, or its header line was removed. `read_recording` refuses a file without a header rather than load pins whose recording route is unknown.

### An old recording no longer decodes

`read_recording` returns events exactly as stored. If an event type in the recording has since gained a schema version, pass your upcasters: `ReplayPins.from_events(recording.events, types, upcasts=upcasts)`. See [How to Evolve Event Schemas with Upcasters](evolve-event-schemas.md).

!!! info "Logging"
    The library logs every model call (drawn or reused from a pin) under the `anagrapheus` logger and never configures logging itself. Turn it up with `logging.getLogger("anagrapheus").setLevel(logging.INFO)` and a handler of your choice.

## Related

- [How to Pin Model and Tool Calls](pin-model-and-tool-calls.md): tools, retries, streaming and custom call policies.
- [Pinning and Replay](../explanation/pinning-and-replay.md): why keys are built from what was asked, and why a miss is a feature.
- [Getting Started](../tutorials/getting-started.md): the same record-and-replay loop as a guided lesson.

[pin-ledger]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger
[pin-ledger-commit]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger.commit
[replay-pins]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[replay-from-events]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins.from_events
[divergence]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.Divergence
[pinned-model]: ../reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[pin-events]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.pin_events
[write-recording]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.write_recording

<!-- INTERNAL NOTES FOR EDITOR - DO NOT INCLUDE IN RENDERED OUTPUT
## Page Classification
- Type: how-to
- Reference used: skills/diataxis/references/how-to-guides.md

## Source References
- src/anagrapheus/__init__.py (register_builtin_events)
- src/anagrapheus/timeline/recording.py, replay.py, pins.py
- src/anagrapheus/provider/model.py
- tests/timeline/test_recording.py, tests/provider/test_model.py (usage patterns)
- Verified script: scratchpad docs-examples/howto/record_replay.py (+ record_replay_errors.py for troubleshooting outputs)

## Accuracy Notes
- Inventory said "replay with ReplayPins.from_events on a fresh store": ReplayPins needs no store at all; the fresh store is only where the replayed unit re-appends its domain events. Wrote it that way.
- FunctionModel's model_id includes the wrapped function's name ("function:function:uk_negotiator:"), so a replay must wrap a function with the same name. Verified: a renamed function misses.
- Record mode (on_miss="record") keeps fresh draws in the ReplayPins object only; nothing is committed to the log except the optional ReplayDiverged via announce_to. Verified.
- PinnedModel's signature annotates wrapped: Model, but WrapperModel.__init__ accepts a KnownModelName string and infers it. Construction verified with a dummy ANTHROPIC_API_KEY; no live call was made. The "anthropic:claude-sonnet-4-5" snippet is construction-verified only.
- UnknownEventType message verified verbatim with a registry that had only timeline events.

## Known Limitations
- The top-level `anagrapheus.register_builtin_events` lives in the package __init__, which gen_ref_pages.py skips, so it has no generated reference entry. Left as plain code (no link) on purpose.

## API Reference Links Used
- anagrapheus.timeline.pins.PinLedger, PinLedger.commit
- anagrapheus.timeline.replay.ReplayPins, ReplayPins.from_events, Divergence
- anagrapheus.provider.model.PinnedModel
- anagrapheus.timeline.recording.pin_events, write_recording
-->
