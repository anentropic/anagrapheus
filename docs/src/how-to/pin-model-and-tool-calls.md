---
title: How to Pin Model and Tool Calls
description: Wrap a pydantic-ai model and its tools so every call is drawn once, reused on retry and answered from the log on replay
---

# How to Pin Model and Tool Calls

This guide shows how to route an agent's model requests and tool invocations through the pin ledger, so that each one is drawn once, reused if the unit of work retries, and answered from the log when the run is replayed. It also covers how to decide what counts as "the same call", which is the one judgment pinning cannot make for you.

**Before you start**

- `anagrapheus` installed and an existing pydantic-ai `Agent`.
- The vocabulary, briefly. A **pin** is a recorded result of a nondeterministic call, stored under a **key** derived from what the call asked. A **unit of work** is the group of calls you commit or abandon together, such as one negotiating turn. While the unit is open, its pins sit in a scratch area. **Committing** promotes them into the log; **abandoning** discards them. See [Pinning and Replay](../explanation/pinning-and-replay.md) for the full picture.

## Who decides what

| The application decides | The library does |
|---|---|
| What counts as the same call (the call policy) | Hashing the policy's material into a key |
| Which tools to pin, and which must stay live | Drawing each key once and reusing it |
| Where a unit of work begins and ends | Keeping uncommitted pins out of the log |
| Whether to commit or abandon a unit | Promoting or discarding pins atomically |

## 1. Wrap the model

Wrap the agent's model in [`PinnedModel`][pinned-model] and give it a resolver: a [`PinLedger`][pin-ledger] for a live run, or [`ReplayPins`][replay-pins] for a replay. Everything else about the agent stays the same.

## 2. Wrap the tools that read the world

A pinned model with unpinned tools replays what the agent *said* while re-running what it *did*: the fetch repeats, and replay sees today's data instead of the data the run acted on. Wrap such tools with [`pinned_tool`][pinned-tool]:

```python title="pin_calls.py"
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel, pinned_tool
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    PinResolver,
    ReplayPins,
    SQLiteEventStore,
)

draws = {"model": 0, "tool": 0}


def negotiator(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    draws["model"] += 1
    if len(messages) == 1:  # first step: look the quota up
        return ModelResponse(parts=[ToolCallPart("current_quota", {"stock": "mackerel"})])
    return ModelResponse(parts=[TextPart("Propose 12% below the current quota.")])


def current_quota(stock: str) -> dict[str, int]:
    """Fetch this year's total allowable catch, in tonnes."""
    draws["tool"] += 1
    return {"stock_tonnes": 794_920}


def build_agent(resolver: PinResolver) -> Agent:
    agent = Agent(PinnedModel(FunctionModel(negotiator), resolver))
    agent.tool_plain(pinned_tool(resolver, current_quota))
    return agent


types = EventTypeRegistry()
register_builtin_events(types)
store = SQLiteEventStore(":memory:")

ledger = PinLedger(store, types)
live = build_agent(ledger).run_sync("UK: open on mackerel.")
ledger.commit()
print(live.output, draws)
# Propose 12% below the current quota. {'model': 2, 'tool': 1}

replay = ReplayPins.from_events(store.read(), types)
replayed = build_agent(replay).run_sync("UK: open on mackerel.")
print(replayed.output, draws)
# Propose 12% below the current quota. {'model': 2, 'tool': 1}
```

The run makes two model requests (the tool call, then the answer) and one tool call, and each is pinned separately. The pin attaches **per request**, not per agent run, so the whole tool loop replays and the counters do not move.

`pinned_tool` keeps the function's signature, so pydantic-ai builds the same tool schema and the model sees no difference. The key is the tool's name and its arguments. A `RunContext` first parameter is left out of the key, because it is filled from the run rather than by the model.

!!! warning "Which tools to pin is your call"
    Pin a tool that reads the world (a search, a fetch, a database query), so replay sees what the run saw. A pure function needs no pin. Do **not** pin a tool whose point is freshness, such as a clock or a live price, if your replay is meant to see the current value. A pinned tool's result must be JSON-serialisable, because it is what gets recorded. An unserialisable result fails at the draw.

## 3. Decide what "the same call" means

The **call policy** reduces a model request to the material its key is hashed from. Too much material and every call gets a fresh key, so nothing is ever reused. Too little and two different calls share one answer. Neither failure raises an error, which is why the choice is explicit.

=== "Default policy"

    Without a `policy` argument, `PinnedModel` uses [`SemanticCallPolicy`][semantic-policy]. It keeps:

    - the model id;
    - the conversation's content, part by part;
    - the request parameters: tool definitions (sorted by name), output schema and instructions;
    - settings that change the draw, such as `temperature`, `max_tokens`, or reasoning settings sent in `extra_body`.

    It drops what differs between two runs of the same call: timestamps, part and tool-call ids, provider names and details, metadata, delivery settings (`timeout`, `extra_headers`, `service_tier` and its provider-prefixed forms such as `openai_service_tier`, prompt-cache options) and session or user ids: `openai_user`, the `user_id` in `anthropic_metadata`, and inside `extra_body` the `session_id`, `user`, `safety_identifier` and `prompt_cache_key` keys and a `metadata` object's `user_id`. So you can send OpenRouter a per-game `session_id` without every new game missing its pins.

    ```python
    PinnedModel(FunctionModel(negotiator), ledger)  # SemanticCallPolicy()
    ```

=== "Custom policy"

    Implement the [`CallPolicy`][call-policy] protocol: one `material` method returning JSON-shaped data. Building on the default is usually easiest. This policy treats every provider route to the negotiator model as the same model, so a recording made directly replays through a gateway:

    ```python title="pin_policy.py"
    from collections.abc import Mapping, Sequence

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from pydantic_ai.settings import ModelSettings

    from anagrapheus import register_builtin_events
    from anagrapheus.provider import PinnedModel, SemanticCallPolicy
    from anagrapheus.timeline import (
        Json,
        EventTypeRegistry,
        PinLedger,
        ReplayPins,
        SQLiteEventStore,
    )


    class SameNegotiator:
        """Treat every route to the negotiator model as the same model."""

        def __init__(self, route_name: str) -> None:
            self._default = SemanticCallPolicy()
            self._route_name = route_name

        def material(
            self,
            messages: Sequence[ModelMessage],
            model_id: str,
            parameters: ModelRequestParameters,
            settings: ModelSettings | None,
        ) -> Mapping[str, Json]:
            material = dict(self._default.material(messages, model_id, parameters, settings))
            material["model"] = self._route_name
            return material


    def direct(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Propose a 12% cut.")])


    def via_gateway(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("replay must not reach the model")


    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(":memory:")
    policy = SameNegotiator("negotiator")

    ledger = PinLedger(store, types)
    Agent(PinnedModel(FunctionModel(direct), ledger, policy=policy)).run_sync("UK: open.")
    ledger.commit()

    replay = ReplayPins.from_events(store.read(), types)
    gateway = PinnedModel(FunctionModel(via_gateway), replay, policy=policy)
    print(Agent(gateway).run_sync("UK: open.").output)
    # Propose a 12% cut.
    ```

    Use the same policy when recording and when replaying. A policy is part of what a key means.

!!! tip "Leave volatile data out of the prompt"
    The policy can only drop fields it can see. A run id, a timestamp or an event id rendered *into the prompt text* is part of what the model was asked, so it changes the key on every run and replay misses on every call. Render domain content, not bookkeeping.

## 4. Commit or abandon the unit of work

Within one unit of work, a call that is repeated with the same key reuses its pinned draw instead of calling the model again. That is what makes a retry after a crash or a timeout cheap and consistent:

```python title="pin_retry.py"
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import EventTypeRegistry, PinLedger, SQLiteEventStore

draws = 0


def norway(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    global draws
    draws += 1
    return ModelResponse(parts=[TextPart(f"Hold at 5% (draw {draws}).")])


types = EventTypeRegistry()
register_builtin_events(types)
store = SQLiteEventStore(":memory:")

keys: list[str] = []
ledger = PinLedger(store, types)
agent = Agent(PinnedModel(FunctionModel(norway), ledger, on_key=keys.append))

first = agent.run_sync("Norway: respond to the UK offer.")
retry = agent.run_sync("Norway: respond to the UK offer.")  # the same call, retried
print(retry.output, "| draws:", draws, "| same key:", keys[0] == keys[1])
# Hold at 5% (draw 1). | draws: 1 | same key: True

ledger.abandon(reason="the response broke a red line")
print(list(store.read())[-1].envelope.type)
# PinsAbandoned

fresh = PinLedger(store, types)  # a new unit of work draws again
again = Agent(PinnedModel(FunctionModel(norway), fresh)).run_sync(
    "Norway: respond to the UK offer."
)
print(again.output)
# Hold at 5% (draw 2).
```

Close every unit one of two ways:

- [`ledger.commit()`][pin-ledger-commit] promotes every pin into the log as [`PinPromoted`][pin-promoted] events, in one transaction. Do this when the unit's outcome stands; replay reads these events.
- [`ledger.abandon(reason=...)`][pin-ledger-abandon] drops the pins and appends a single [`PinsAbandoned`][pins-abandoned] event recording what was discarded and how many attempts each key took. Do this when you reject the outcome and want a genuinely new draw: the next unit of work, with a new `PinLedger`, draws again.

The `on_key` hook receives the key of every request. Use it to log which pin produced a result, or to compare keys when calls are not being reused.

### Resume a unit after a crash

The scratch area lives in the same SQLite file as the log, so uncommitted pins survive a crash. Save the unit's id when it starts, and reopen the ledger with it to reuse what was already drawn:

```python
ledger = PinLedger(store, types)
saved_unit_id = ledger.unit_id  # persist this alongside your own state

# ...after a restart, over the same database file:
ledger = PinLedger(store, types, unit_id=saved_unit_id)
```

## 5. Stream as usual

Streaming needs no extra wiring. `PinnedModel` pins a streamed response as the response it accumulated to, and replays it as a stream rebuilt from that response:

```python title="pin_stream.py"
import asyncio
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
)

opened = 0


async def negotiator(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    global opened
    opened += 1
    for chunk in ["We hold ", "at a ", "5% cut."]:
        yield chunk


async def speak(resolver) -> str:
    agent = Agent(PinnedModel(FunctionModel(stream_function=negotiator), resolver))
    async with agent.run_stream("Norway: respond to the UK offer.") as result:
        return await result.get_output()


async def main() -> None:
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(":memory:")

    ledger = PinLedger(store, types)
    print(await speak(ledger), "| streams opened:", opened)
    ledger.commit()

    replay = ReplayPins.from_events(store.read(), types)
    print(await speak(replay), "| streams opened:", opened)


asyncio.run(main())
# We hold at a 5% cut. | streams opened: 1
# We hold at a 5% cut. | streams opened: 1
```

The key is the same one a non-streamed request would build, because streaming is how the answer is received, not part of what was asked. A run recorded with streaming replays without it, and the other way round.

??? info "What a replayed stream does not reproduce"
    Replay restores the response's parts, usage, finish reason and state, including the state of a stream that was abandoned part-way, which pins as the partial response it was. It does not restore chunk boundaries or timing: those were the provider's and are not in the pin. Each part is surfaced whole, in order. Code that acts on individual chunk boundaries sees different boundaries on replay. See [`PinnedStreamedResponse`][pinned-stream].

## Verify it worked

Count calls, as the examples do. Wrap the model function in a counter (a `FunctionModel` delegate is the simplest place) and assert that:

- a retry within one unit does not increase the count;
- a strict replay over the committed log leaves the count unchanged.

Run with `logging.getLogger("anagrapheus").setLevel(logging.INFO)` and every call is logged as either `drawn` or `reused from pin`, with its key.

## Troubleshooting

### Every call draws fresh and nothing is reused

The key changes between calls that you think are the same. Pass `on_key=print` and compare keys. The usual cause is something volatile in the prompt itself (a timestamp, a counter, an id). A custom policy that keeps a field the default drops would have the same effect.

### Two different calls return the same answer

The policy drops something that changes the draw. With the default policy this should not happen. With a custom one, check that `material` still includes the messages, the model and the request parameters.

### `PinMiss` on replay after renaming a tool

Tool definitions are part of the key, because the set of tools changes what the model can do. Register tools under the same names, and with the same descriptions and argument schemas, as when the run was recorded.

### A `PinsAbandoned` event in the log

This is the audit trail of an abandoned unit of work: its `reason`, and each discarded key with its attempt count. It is not an error. Replay ignores it.

## Related

- [How to Record a Run and Replay It Offline](record-and-replay.md): writing pins to a recording file.
- [How to Assemble Prompts from Granted Slices](assemble-prompts.md): building prompts that render the same bytes on replay.
- [Pinning and Replay](../explanation/pinning-and-replay.md): keys, reuse, promotion and abandonment explained.

[pinned-model]: ../reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[pinned-tool]: ../reference/anagrapheus/provider/tools.md#anagrapheus.provider.tools.pinned_tool
[pinned-stream]: ../reference/anagrapheus/provider/stream.md#anagrapheus.provider.stream.PinnedStreamedResponse
[call-policy]: ../reference/anagrapheus/provider/policy.md#anagrapheus.provider.policy.CallPolicy
[semantic-policy]: ../reference/anagrapheus/provider/policy.md#anagrapheus.provider.policy.SemanticCallPolicy
[pin-ledger]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger
[pin-ledger-commit]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger.commit
[pin-ledger-abandon]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger.abandon
[replay-pins]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[pin-promoted]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinPromoted
[pins-abandoned]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinsAbandoned
