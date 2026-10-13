---
title: "Getting Started: Record and Replay an Agent"
description: Record a two-agent exchange to an event log, write it to a file, and replay it offline without calling the model.
difficulty: beginner
---

# Getting Started: Record and Replay an Agent

In this tutorial you record a short exchange between two negotiating agents, save the recording to a file, and replay it in a separate process without calling the model once. Then you change a prompt and watch the replay refuse to pretend it is still a replay.

By the end you will have:

- wrapped pydantic-ai models so that every request is *pinned*: drawn once, recorded under a key built from what was asked, and reused after that
- committed the pinned results to an event log in SQLite
- written a recording file and replayed it on a fresh process
- seen a [`PinMiss`][pin-miss] when the code asks the model something the recording never saw

**Time:** about 15 minutes.
**Prerequisites:** Python 3.14 or later, and familiarity with pydantic-ai agents. No API key is needed: the agents run on pydantic-ai's `FunctionModel`.

## Set up a project

Create a directory for the tutorial and install anagrapheus into a virtual environment.

=== "pip"

    ```bash
    mkdir summit && cd summit
    python3.14 -m venv .venv
    source .venv/bin/activate
    pip install anagrapheus
    ```

=== "uv"

    ```bash
    mkdir summit && cd summit
    uv init --python 3.14 --bare
    uv add anagrapheus
    source .venv/bin/activate
    ```

Check that the package imports:

```bash
python -c "import anagrapheus; print('ready')"
```

You should see `ready`.

!!! tip "The pydantic-ai banner"

    pydantic-ai prints a banner to stderr the first time an agent runs. Set `PYDANTIC_AI_NO_BANNER=1` in your shell to hide it. The outputs on this page are shown without it.

## Step 1: Write the summit

The summit is two agents, one for the EU and one for the UK. The EU opens the talks on a fisheries clause, and the UK replies to what the EU said. Each agent's model is a `FunctionModel` that returns a fixed sentence and counts how often it is really called.

Create `summit.py`:

```python title="summit.py" hl_lines="23-26 29 32 36"
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import EventTypeRegistry, PinResolver

#: How many times each delegate's model was really called.
calls = {"eu": 0, "uk": 0}


def eu_delegate(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    calls["eu"] += 1
    return ModelResponse(parts=[TextPart("The EU proposes a 60/40 split of the quota.")])


def uk_delegate(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    calls["uk"] += 1
    return ModelResponse(parts=[TextPart("The UK will not go below 70/30.")])


def event_types() -> EventTypeRegistry:
    types = EventTypeRegistry()
    register_builtin_events(types)
    return types


def run_summit(resolver: PinResolver) -> list[str]:
    eu_model, uk_model = FunctionModel(eu_delegate), FunctionModel(uk_delegate)
    eu = Agent(
        PinnedModel(eu_model, resolver),
        instructions="You negotiate for the EU. Answer in one sentence.",
    )
    uk = Agent(
        PinnedModel(uk_model, resolver),
        instructions="You negotiate for the UK. Answer in one sentence.",
    )
    opening = eu.run_sync("Open the talks on the fisheries clause.").output
    reply = uk.run_sync(f"The EU said: {opening} Reply.").output
    return [f"EU: {opening}", f"UK: {reply}"]
```

Three pieces of this file belong to anagrapheus:

- `event_types()` builds an [`EventTypeRegistry`][registry], the list of event types the log can hold, and registers the library's own events into it with `register_builtin_events`. The pinned results are stored as library events, so both recording and replay need them registered.
- Each agent's model is wrapped in a [`PinnedModel`][pinned-model]. The agent code is ordinary pydantic-ai; only the model changes.
- `run_summit` takes a *resolver*, the object that decides what happens when a pinned model makes a request. You will pass it two different resolvers: one that records and one that replays. The summit code stays the same for both.

## Step 2: Record a run

Recording uses a [`PinLedger`][pin-ledger]. On each request it builds the key, calls the model once, and holds the result in a scratch area of the store. When the unit of work is finished, `commit()` promotes the held results into the log as ordinary events.

Create `record.py`:

```python title="record.py"
from pathlib import Path

from anagrapheus.timeline import PinLedger, SQLiteEventStore, pin_events, write_recording

from summit import calls, event_types, run_summit

types = event_types()
store = SQLiteEventStore("summit.db")
ledger = PinLedger(store, types)

for line in run_summit(ledger):
    print(line)

ledger.commit()
written = write_recording(
    Path("summit.jsonl"),
    pin_events(store.read()),
    header={"story": "fisheries summit", "models": "scripted"},
)
print(f"model calls: {calls}")
print(f"recorded {written} pins to summit.jsonl")
store.close()
```

Run it:

```bash
python record.py
```

You should see:

```text
EU: The EU proposes a 60/40 split of the quota.
UK: The UK will not go below 70/30.
model calls: {'eu': 1, 'uk': 1}
recorded 2 pins to summit.jsonl
```

Each delegate was called once, and each call left one pin. Two files now exist:

- `summit.db` is the event log, a SQLite file opened by [`SQLiteEventStore`][sqlite-store].
- `summit.jsonl` is the recording. [`pin_events`][pin-events] picks out only the pinned results from the log, and [`write_recording`][write-recording] writes them after a header line saying what produced them.

Look at the first two lines of the recording:

```bash
head -n 2 summit.jsonl
```

The first line is the header you passed in. The second is the EU's pinned response, stored as an event with a `key` that starts `p1:model_call:` and a `result` holding the model's reply.

!!! warning "Each run of `record.py` adds to `summit.db`"

    The log is append-only, so running `record.py` a second time adds a second run's pins to the same file, and the recording then holds four pins. Delete `summit.db` before you record again to start from an empty log.

## Step 3: Replay the run

Replay uses [`ReplayPins`][replay-pins]. It reads the pinned results from a sequence of events and answers each request from them. It is in *strict* mode by default: any request it has no pin for raises an error before the model is called.

Create `replay.py`:

```python title="replay.py"
from pathlib import Path

from anagrapheus.timeline import ReplayPins, read_recording

from summit import calls, event_types, run_summit

recording = read_recording(Path("summit.jsonl"))
replay = ReplayPins.from_events(recording.events, event_types())

for line in run_summit(replay):
    print(line)

print(f"recorded by: {dict(recording.header)}")
print(f"model calls: {calls}")
```

Run it:

```bash
python replay.py
```

You should see:

```text
EU: The EU proposes a 60/40 split of the quota.
UK: The UK will not go below 70/30.
recorded by: {'models': 'scripted', 'story': 'fisheries summit'}
model calls: {'eu': 0, 'uk': 0}
```

The exchange is the same, and neither model was called. This is a new process that never opened `summit.db`: everything it needed came from the recording file. [`read_recording`][read-recording] checks that the file has a recording header and gives you the header back alongside the events.

## Step 4: Change a prompt and replay again

A replay is only a replay while the code asks the same questions. Open `summit.py` and, in `run_summit`, change the UK agent's instructions:

```python title="summit.py" hl_lines="3"
uk = Agent(
    PinnedModel(uk_model, resolver),
    instructions="You negotiate for the UK. Answer in one short sentence.",
)
```

Run the replay again:

```bash
python replay.py
```

This time it stops with a traceback that ends:

```text
anagrapheus.timeline.replay.PinMiss: replay diverged at key 'p1:model_call:6971ba00…' (model_call) after 1 hit(s)
```

The EU's request was unchanged, so it was answered from the recording: that is the one hit. The UK's request now carries different instructions, so its key is different, and the recording has no pin for it. Strict replay raises [`PinMiss`][pin-miss] at that point, before the UK model is called.

The error carries a [`Divergence`][divergence] that says where the replay stopped. Create `diverge.py` to read it:

```python title="diverge.py"
from pathlib import Path

from anagrapheus.timeline import PinMiss, ReplayPins, read_recording

from summit import calls, event_types, run_summit

recording = read_recording(Path("summit.jsonl"))
replay = ReplayPins.from_events(recording.events, event_types())

try:
    run_summit(replay)
except PinMiss as miss:
    print(f"kind: {miss.divergence.kind}")
    print(f"pins answered before the miss: {miss.divergence.after_hits}")
    print(f"model calls: {calls}")
```

Run it:

```bash
python diverge.py
```

You should see:

```text
kind: model_call
pins answered before the miss: 1
model calls: {'eu': 0, 'uk': 0}
```

No model was reached, not even for the request that missed. Now change the UK's instructions back to `"You negotiate for the UK. Answer in one sentence."` and run `python replay.py` once more. The replay succeeds again.

## Step 5: Record against a real model

Everything so far ran offline. To record the same summit against a real provider, only the line that builds the models changes. This step uses Anthropic, and needs the `anthropic` extra and an API key.

Install the extra and set your key:

=== "pip"

    ```bash
    pip install "anagrapheus[anthropic]"
    export ANTHROPIC_API_KEY=...  # your key
    ```

=== "uv"

    ```bash
    uv add "anagrapheus[anthropic]"
    export ANTHROPIC_API_KEY=...  # your key
    ```

In `summit.py`, replace the model line in `run_summit`:

=== "Offline"

    ```python
    eu_model, uk_model = FunctionModel(eu_delegate), FunctionModel(uk_delegate)
    ```

=== "Anthropic"

    ```python
    from pydantic_ai.models import infer_model  # add this to the imports at the top

    eu_model = uk_model = infer_model("anthropic:claude-haiku-5-5")
    ```

In `record.py`, change the header to say which model made the recording, `header={"story": "fisheries summit", "models": "anthropic:claude-haiku-5-5"}`, so that anyone who opens the file later knows what it needs. Then delete the old log, record, and replay:

```bash
rm summit.db
python record.py
python replay.py
```

`record.py` now makes two real model calls, and the agents say whatever the model said. `replay.py` prints the same two lines without calling the provider. Your `calls` counter reads zero both times now, because the delegate functions are no longer used. The proof that nothing was reached is that the replay finished at all: in strict mode, any request without a pin raises before the provider is called.

!!! info "The model is part of the key"

    The pin key includes the model's id, so a recording made with `FunctionModel` does not replay against `anthropic:claude-haiku-5-5`, and the other way round: every request misses. `replay.py` still builds the Anthropic model, so it still needs `ANTHROPIC_API_KEY` set, even though the model is never called.

## What you've learned

You have recorded an agent run and replayed it exactly. You now know how to:

- wrap any pydantic-ai model in `PinnedModel` so that each request goes through a resolver
- record with `PinLedger` and promote its pins into the log with `commit()`
- save the pins to a file with `write_recording` and load them with `read_recording`
- replay with `ReplayPins`, which never calls the model in strict mode
- read a `PinMiss` and its `Divergence` when the code asks something new

## Next steps

- [Tour the negotiation example](negotiation-example.md) to see pinning working alongside beliefs, identity, the chronicle and prompt assembly.
- [Record a run and replay it offline](../how-to/record-and-replay.md) covers the same steps for your own application, with troubleshooting.
- [Pin model and tool calls](../how-to/pin-model-and-tool-calls.md) adds [`pinned_tool`][pinned-tool] and custom call policies.
- [Pinning and replay](../explanation/pinning-and-replay.md) explains how keys are built and why a miss is reported rather than redrawn.

[registry]: ../reference/anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry
[pinned-model]: ../reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[pin-ledger]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger
[sqlite-store]: ../reference/anagrapheus/timeline/sqlite.md#anagrapheus.timeline.sqlite.SQLiteEventStore
[pin-events]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.pin_events
[write-recording]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.write_recording
[read-recording]: ../reference/anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.read_recording
[replay-pins]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[pin-miss]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.PinMiss
[divergence]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.Divergence
[pinned-tool]: ../reference/anagrapheus/provider/tools.md#anagrapheus.provider.tools.pinned_tool
