"""
Milestone-4 acceptance: the provider layer, end to end, one story.

An agent is asked a question, calls a tool to answer it, and returns a structured
result. Every draw — two model requests and one tool invocation — is pinned, committed
into the log, and then the whole run is replayed with the model **rigged to fail if it
is reached at all**, and the tool's side effect not repeated.

That last part is the milestone. Until now the pin ledger had never pinned a real
effect: doc 00 §4's argument was asserted against synthetic callables. Here the whole
run comes back from the log.

The model is not *swapped out* for the replay, which is the obvious way to write this
and the wrong one: model identity is part of the pin key, so a substituted model would
miss for that reason and prove nothing. It stays, and asserts it is never called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.provider import PinnedModel, pinned_tool
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
    register_builtin_events,
)
from anagrapheus.timeline.replay import PinMiss

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


class Answer(BaseModel):
    """The structured output the agent is asked for."""

    city: str


def test_milestone_4_acceptance(tmp_path: Path):
    path = tmp_path / "agent.db"
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(path)
    ledger = PinLedger(store, types)

    model_calls: list[int] = []
    lookups: list[str] = []
    # Flipped after the live run. The model object itself must stay the *same* — its
    # identity is part of the key, so substituting a different function would make the
    # replay miss for that reason and prove nothing.
    reachable = True

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert reachable, "the replayed run reached the model"
        model_calls.append(len(model_calls))
        if len(model_calls) == 1:
            return ModelResponse(parts=[ToolCallPart("capital_of", {"country": "Portugal"})])
        return ModelResponse(parts=[TextPart('{"city": "Lisbon"}')])

    def capital_of(country: str) -> str:
        # The world-touching half. A replay that re-ran this would be repeating the
        # run, not replaying it.
        lookups.append(country)
        return "Lisbon"

    # -- the live run: the model is asked twice, the tool once ------------------------
    agent = Agent(model=PinnedModel(FunctionModel(reply), ledger), output_type=Answer)
    agent.tool_plain(pinned_tool(ledger, capital_of))

    result = agent.run_sync("what is the capital of Portugal?")

    assert result.output == Answer(city="Lisbon")
    assert len(model_calls) == 2
    assert lookups == ["Portugal"]

    # -- commit: every draw becomes an ordinary event in the log ----------------------
    ledger.commit()
    kinds = [str(e.payload["kind"]) for e in store.read() if e.envelope.type == "PinPromoted"]
    assert sorted(kinds) == ["model_call", "model_call", "tool_call"]
    events = list(store.read())
    store.close()

    # -- replay: the model would raise if reached, and the tool must not fire again ---
    reachable = False
    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed = Agent(model=PinnedModel(FunctionModel(reply), replay), output_type=Answer)
    replayed.tool_plain(pinned_tool(replay, capital_of))

    again = replayed.run_sync("what is the capital of Portugal?")

    assert again.output == Answer(city="Lisbon")
    assert len(model_calls) == 2
    assert lookups == ["Portugal"]
    assert replay.divergence is None


def test_a_changed_question_is_announced_rather_than_replayed(tmp_path: Path):
    """
    Doc 00 §4c's honest half, with a real agent.

    Generation-replay is best-effort: change the prompt assembly and the keys miss. The
    point is that the miss is *detectable* — the run is marked divergent at the first
    one, so you get a new run continuing the same history, announced.
    """
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(tmp_path / "agent.db")
    ledger = PinLedger(store, types)

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Lisbon")])

    Agent(model=PinnedModel(FunctionModel(reply), ledger)).run_sync("where?")
    ledger.commit()
    events = list(store.read())
    store.close()

    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed = Agent(model=PinnedModel(FunctionModel(reply), replay))

    with pytest.raises(PinMiss):
        replayed.run_sync("where is it really?")

    assert replay.divergence is not None
    assert replay.divergence.kind == "model_call"
