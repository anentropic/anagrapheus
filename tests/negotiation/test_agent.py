"""The agent reads a model-of scope, and its offer moves when that model moves."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.timeline import PinLedger
from examples.negotiation.agent import Negotiator, brief_for
from examples.negotiation.scopes import held, model_of, stated

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    from examples.negotiation.table import Table


def _set_up(table: Table, *, believed: str) -> uuid.UUID:
    """The UK says `never`; the EU believes `believed`; the EU wants to settle."""
    fisheries = table.table_clause("fisheries")
    uk, eu = table.seats["uk"], table.seats["eu"]
    source = uuid.uuid7()
    table.believe(held("eu"), "position", (eu, fisheries), "settle", source)
    table.believe(stated("uk"), "position", (uk, fisheries), "never", source)
    table.believe(model_of("eu", "uk"), "position", (uk, fisheries), believed, source)
    return fisheries


def test_the_brief_carries_all_three_scopes(table: Table):
    """
    A brief built from one scope would read identically with or without a model.

    Asserting the three lines separately because that is what makes the next test
    meaningful: if the model-of line were missing, the agent could not act on it and
    the behavioural test would pass for the wrong reason.
    """
    _set_up(table, believed="concede")

    brief = brief_for(table, "eu", "uk", "fisheries")

    assert "Our position: settle" in brief
    assert "Their stated position: never" in brief
    assert "What we believe they actually want: concede" in brief


def test_the_offer_changes_when_only_the_model_of_scope_changes(table: Table):
    """
    **The milestone's behavioural claim.** Everything held fixed but our belief about
    them, and the agent proposes differently.

    The model here is deterministic and reads the brief, so what varies is the input,
    not the sampling. A single-holder design has nowhere to make this change: it would
    have only the UK's *stated* position, which is `never` in both runs.
    """
    seen: list[str] = []

    def read_the_brief(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = str(messages[-1].parts[-1].content)  # type: ignore[union-attr]
        seen.append(prompt)
        concede = "actually want: concede" in prompt
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result",
                    {
                        "clause": "fisheries",
                        "concede": concede,
                        "because": "read from our model of them",
                    },
                )
            ]
        )

    _set_up(table, believed="concede")
    hopeful = Negotiator(
        FunctionModel(read_the_brief), PinLedger(table.store, table.types), party="eu"
    ).propose(table, "uk", "fisheries")

    # Same table, same stated position, one belief revised.
    uk, fisheries = table.seats["uk"], table.clauses["fisheries"]
    table.believe(model_of("eu", "uk"), "position", (uk, fisheries), "hold_firm", uuid.uuid7())
    wary = Negotiator(
        FunctionModel(read_the_brief), PinLedger(table.store, table.types), party="eu"
    ).propose(table, "uk", "fisheries")

    assert hopeful.concede is True
    assert wary.concede is False
    assert all("Their stated position: never" in prompt for prompt in seen), (
        "the public position never changed -- only what we believed about them"
    )


def test_a_round_replays_without_reaching_the_model(table: Table):
    """The provider layer's guarantee, holding inside the demo rather than a fixture."""
    from anagrapheus.timeline import ReplayPins

    calls: list[int] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(len(calls))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result",
                    {"clause": "fisheries", "concede": True, "because": "settled"},
                )
            ]
        )

    _set_up(table, believed="concede")
    ledger = PinLedger(table.store, table.types)
    live = Negotiator(FunctionModel(reply), ledger, party="eu").propose(table, "uk", "fisheries")
    ledger.commit()

    replay = ReplayPins.from_events(list(table.store.read()), table.types, on_miss="strict")
    again = Negotiator(FunctionModel(reply), replay, party="eu").propose(table, "uk", "fisheries")

    assert again == live
    assert len(calls) == 1, "the replayed round reached no model"


def test_text_output_is_not_what_the_agent_returns(table: Table):
    """Structured output, so a round's result is data the next round can act on."""

    def prose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I think we should concede, probably")])

    _set_up(table, believed="concede")
    negotiator = Negotiator(
        FunctionModel(prose), PinLedger(table.store, table.types), party="eu"
    )

    import pytest
    from pydantic_ai import UnexpectedModelBehavior

    with pytest.raises(UnexpectedModelBehavior):
        negotiator.propose(table, "uk", "fisheries")
