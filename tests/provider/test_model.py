"""The pinned model: one draw per call, reuse thereafter, and no silent streaming."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters

from anagrapheus.provider import PIN_KIND, PinnedModel
from anagrapheus.timeline import ReplayPins
from anagrapheus.timeline.replay import PinMiss

from .conftest import Counted, CountedStream, Rig, says

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


def test_the_same_call_draws_once(rig: Rig):
    """
    The whole point, stated as a count.

    The second run reaches the same key, so the model is never asked again — and the
    answer is the recorded one rather than a fresh draw that happened to agree.
    """
    counted = Counted(says("Lisbon"))
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    first = agent.run_sync("where?")
    second = agent.run_sync("where?")

    assert first.output == second.output == "Lisbon"
    assert counted.count == 1


def test_a_different_prompt_is_a_different_key(rig: Rig):
    """Reuse must not be so eager that two different questions share an answer."""
    counted = Counted(says("Lisbon"))
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    agent.run_sync("where?")
    agent.run_sync("when?")

    assert counted.count == 2


def test_the_pin_promotes_into_the_log(rig: Rig):
    """A committed unit of work leaves the draw in the log as an ordinary event."""
    counted = Counted(says("Lisbon"))
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))
    agent.run_sync("where?")

    rig.ledger.commit()

    promoted = [event for event in rig.store.read() if event.envelope.type == "PinPromoted"]
    assert len(promoted) == 1
    assert promoted[0].payload["kind"] == PIN_KIND


def test_replay_answers_without_reaching_the_model(rig: Rig):
    """
    Generation-replay, and the assertion that makes it real: **zero** model calls.

    Under strict replay the effect is never awaited at all — a miss raises before the
    call is made — so "no model was reached" is something the test holds rather than
    hopes.
    """
    counted = Counted(says("Lisbon"))
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    live.run_sync("where?")
    rig.ledger.commit()
    drawn = counted.count

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    result = replayed.run_sync("where?")

    assert result.output == "Lisbon"
    assert counted.count == drawn == 1


def test_a_changed_prompt_diverges_rather_than_pretending(rig: Rig):
    """
    A replay that meets a key it has no answer for stops being a replay, and says so.

    This is doc 00 §4c's honest half: you get a new run continuing the same history,
    announced — not a different run wearing the word "replay".
    """
    counted = Counted(says("Lisbon"))
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    live.run_sync("where?")
    rig.ledger.commit()

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    with pytest.raises(PinMiss):
        replayed.run_sync("somewhere else?")
    assert replay.divergence is not None
    assert replay.divergence.kind == PIN_KIND


def test_streaming_is_pinned_rather_than_refused_or_passed_through(rig: Rig):
    """
    The two wrong answers, ruled out together.

    Passing a stream through unpinned would put a live unrecorded draw inside a run that
    believes every draw is pinned. Refusing it — which this library did until M5 — kept
    that honest but made the whole streaming surface unusable, including
    ``run_stream_events`` and an ``event_stream_handler`` on an ordinary ``run()``,
    since all three route through ``request_stream``. Neither is needed: the stream is
    opened once and replayed from what it accumulated.
    """
    counted = CountedStream(["Lis", "bon"])
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    async def stream() -> str:
        async with agent.run_stream("where?") as result:
            return await result.get_output()

    import asyncio

    assert asyncio.run(stream()) == "Lisbon"
    assert asyncio.run(stream()) == "Lisbon"
    assert counted.opens == 1


def test_a_tool_loop_pins_every_step(rig: Rig):
    """
    Several draws in one run, so several keys — which is why the pin is per request.

    Pinning the run's final answer instead would replay the conclusion while re-running
    every step that reached it.
    """
    steps: list[int] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        steps.append(len(steps))
        if len(steps) == 1:
            return ModelResponse(parts=[ToolCallPart("lookup", {"what": "capital"})])
        return ModelResponse(parts=[TextPart("Lisbon")])

    counted = Counted(reply)
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    @agent.tool_plain
    def lookup(what: str) -> str:  # pyright: ignore[reportUnusedFunction] -- the agent calls it
        return "Portugal"

    result = agent.run_sync("where?")
    rig.ledger.commit()

    assert result.output == "Lisbon"
    assert counted.count == 2
    promoted = [e for e in rig.store.read() if e.envelope.type == "PinPromoted"]
    assert len(promoted) == 2

    # ...and the whole loop replays without the model being reached once. The tool must
    # be registered under the same name: the available tool set changes what the model
    # can do, so the policy keys on it, and a renamed tool is a different call.
    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    @replayed.tool_plain(name="lookup")
    def lookup_on_replay(what: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return "Portugal"

    before = counted.count
    assert replayed.run_sync("where?").output == "Lisbon"
    assert counted.count == before


def test_re_interpreting_a_pin_never_re_draws(rig: Rig):
    """
    §4a's property, now with a real ``ModelResponse`` rather than a synthetic value.

    Changing what you do with a result must not cost a fresh draw — that is the whole
    reason the key excludes the interpretation.
    """
    counted = Counted(says("Lisbon"))
    model = PinnedModel(counted.model(), rig.ledger)
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="where?")])]

    import asyncio

    first = asyncio.run(model.request(messages, None, ModelRequestParameters()))
    second = asyncio.run(model.request(messages, None, ModelRequestParameters()))

    assert counted.count == 1
    assert isinstance(first, ModelResponse)
    assert first.parts[0] == second.parts[0]
