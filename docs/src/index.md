---
title: Overview
description: What anagrapheus is, who it is for, how to install it, and a first record-then-replay run.
---

# anagrapheus

anagrapheus gives a long-running LLM agent one authoritative event log, and builds everything else (beliefs, entity identity, a consolidated history, assembled prompts) as state derived from that log. Every model call made through `PinnedModel`, and every tool wrapped with `pinned_tool`, is recorded in the log, so a past run can be replayed exactly, offline, without reaching the model again.

It is for Python developers who build agents or simulations that run for many turns, such as negotiations, games with non-player characters, or interactive fiction, where several agents hold private beliefs about each other and where a run is too expensive or too random to repeat by re-running it.

## Two ideas

anagrapheus rests on two ideas. The rest of the library follows from them.

**One log; everything else is a fold.** The event log is append-only and is the only thing that is authoritative. A *fold* is a function that applies events in log order to build some state. The state a fold builds is a *projection*: it can be dropped and rebuilt from the log at any time. Each projection records its *position*, the sequence number of the last event it has consumed, so it can catch up from there. Per-agent beliefs, the entity registry and the chronicle (consolidated history) are all projections.

**Every draw is pinned.** A model call returns a different answer each time, so it is a *draw*. *Pinning* a draw records its result under a key built from what was asked: the conversation, the model, the tools and the settings. A retry with the same key gets the recorded result instead of a new draw. *Replay* answers every request from the recorded results and never calls the model. That is different from re-running, which calls the model again and gets new answers. If your code now asks something different, the key changes and replay stops with a [`PinMiss`][pin-miss] instead of quietly drawing a new answer.

[Read how the layers fit together](explanation/architecture.md){ .md-button }

## Installation

anagrapheus needs Python 3.14 or later. It builds on [pydantic-ai](https://ai.pydantic.dev/) for the model call and on [doxastica](https://pypi.org/project/doxastica/) for storing beliefs.

=== "pip"

    ```bash
    pip install anagrapheus
    ```

=== "uv"

    ```bash
    uv add anagrapheus
    ```

The base install is provider-agnostic, and runs offline with pydantic-ai's `FunctionModel` and `TestModel`. To call a real provider, add the extra for it:

=== "pip"

    ```bash
    pip install "anagrapheus[anthropic]"  # or anagrapheus[openai]
    ```

=== "uv"

    ```bash
    uv add "anagrapheus[anthropic]"  # or anagrapheus[openai]
    ```

The `openai` extra installs the OpenAI SDK, which pydantic-ai uses for OpenAI itself and for OpenAI-compatible routes such as OpenRouter.

## A first taste

This script records one model call, then replays it from the log. It runs offline: the "model" is a pydantic-ai `FunctionModel` that counts how often it is called.

```python title="taste.py"
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import EventTypeRegistry, PinLedger, ReplayPins, SQLiteEventStore

calls = 0


def delegate(messages, info):
    global calls
    calls += 1
    return ModelResponse(parts=[TextPart("The EU proposes a 60/40 split.")])


types = EventTypeRegistry()
register_builtin_events(types)
store = SQLiteEventStore("summit.db")

# Record: each model request is drawn once and pinned, then committed to the log.
ledger = PinLedger(store, types)
agent = Agent(PinnedModel(FunctionModel(delegate), ledger))
print(agent.run_sync("Open the fisheries talks.").output)
ledger.commit()

# Replay: every request is answered from the log, so the model is never called.
replay = ReplayPins.from_events(store.read(), types)
agent = Agent(PinnedModel(FunctionModel(delegate), replay))
print(agent.run_sync("Open the fisheries talks.").output)
print(f"model calls: {calls}")
```

```text
The EU proposes a 60/40 split.
The EU proposes a 60/40 split.
model calls: 1
```

[`PinnedModel`][pinned-model] wraps any pydantic-ai model, so the agent code does not change. It is given a *resolver*, which decides what pinning means: a [`PinLedger`][pin-ledger] draws once and records the result, and [`ReplayPins`][replay-pins] answers from results already in the log. The [Getting Started](tutorials/getting-started.md) tutorial builds on this with two agents, a recording file and a deliberate pin miss.

## What is in the package

The library is six subpackages. Each one is a layer over the same log.

| Subpackage | What it gives you |
|---|---|
| [`anagrapheus.timeline`][ref-timeline] | The event log and its SQLite store, the registry of event types, folds and projections, schema upcasting, the pin ledger, replay and recordings. |
| [`anagrapheus.provider`][ref-provider] | [`PinnedModel`][pinned-model] and [`pinned_tool`][pinned-tool], which send model and tool calls through the pin ledger, and the call policy that decides when two calls are the same call. |
| [`anagrapheus.beliefs`][ref-beliefs] | Per-agent beliefs as a fold over the log, belief ids minted from declared predicates, and an index from entities to the beliefs that mention them. |
| [`anagrapheus.identity`][ref-identity] | Entity identity as decisions recorded in the log (establish, alias, merge, split), and the re-key rows a merge produces. |
| [`anagrapheus.chronicle`][ref-chronicle] | Consolidation of past log ranges into sections that cite the range they cover, read as recent events plus older consolidated history. |
| [`anagrapheus.assembly`][ref-assembly] | Prompt assembly from slices of state that a role was granted, with parts that record the sources they were read from and a budget that never drops a binding part. |

Storage is the standard library's `sqlite3`. One database file can hold every component. The library logs under the `anagrapheus` logger and never configures logging itself.

!!! info "What the library decides, and what it leaves to you"

    The library owns the mechanism: pin keys, pins, folds, positions, and the checks on citations and grants. Your application owns the judgments: what makes two model calls the same call, which predicates exist and what values they take, which scopes (belief holders) there are, whether two names are one entity, how to apply a re-key plan, and how prompts are rendered and budgeted. [How the layers fit together](explanation/architecture.md) sets out the full split.

## Where to go next

- **[Tutorials](tutorials/index.md)**: start with [Getting Started](tutorials/getting-started.md), then [tour the negotiation example](tutorials/negotiation-example.md), which uses every layer.
- **[How-To Guides](how-to/index.md)**: steps for one task at a time, such as [recording and replaying a run](how-to/record-and-replay.md) or [keeping per-agent beliefs](how-to/per-agent-beliefs.md).
- **[Explanation](explanation/index.md)**: why the design is the way it is, starting with [how the layers fit together](explanation/architecture.md) and [pinning and replay](explanation/pinning-and-replay.md).
- **[Reference](reference/index.md)**: the API, generated from the source, one page per module.

[pinned-model]: reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[pinned-tool]: reference/anagrapheus/provider/tools.md#anagrapheus.provider.tools.pinned_tool
[pin-ledger]: reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger
[replay-pins]: reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[pin-miss]: reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.PinMiss
[ref-timeline]: reference/index.md#timeline
[ref-provider]: reference/index.md#provider
[ref-beliefs]: reference/index.md#beliefs
[ref-identity]: reference/index.md#identity
[ref-chronicle]: reference/index.md#chronicle
[ref-assembly]: reference/index.md#assembly
