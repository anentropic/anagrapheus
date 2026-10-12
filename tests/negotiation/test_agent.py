"""
The agent reads a *derived* model of its counterpart, and its offer moves with it.

Since M8 there is no `model-of` scope to read. R28 forbids storing one, so the third
line of the brief is reconstructed from access-beliefs at assembly time and discarded
with the brief. What the agent does is unchanged; where the belief comes from is not.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from examples.negotiation import domain
from examples.negotiation.agent import (
    Negotiator,
    Offer,
    Reading,
    brief_for,
    record_offer,
    record_reading,
)
from examples.negotiation.scopes import held, stated
from examples.negotiation.scripted import scripted
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.timeline import PinLedger

if TYPE_CHECKING:
    from examples.negotiation.table import Table
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


def _set_up(table: Table, *, believed: str) -> uuid.UUID:
    """
    The UK says `never`; the EU comes to believe `believed`; the EU wants to settle.

    The third of those is no longer a row the test writes. It is a remark the EU
    overheard plus the EU's belief that the UK let it slip — and the model falls out of
    those, freshly, every time it is asked for.
    """
    fisheries = table.table_clause("fisheries")
    uk, eu = table.seats["uk"], table.seats["eu"]
    source = uuid.uuid7()
    table.believe(held("eu"), "position", (eu, fisheries), "settle", source)
    table.believe(stated("uk"), "position", (uk, fisheries), "never", source)
    _leak(table, clause=fisheries, suggests=believed)
    table.projector.catch_up()
    return fisheries


def _leak(table: Table, *, clause: uuid.UUID, suggests: str) -> uuid.UUID:
    """A remark by the UK that the EU hears, and believes the UK knows it made."""
    remark = table.types.pending(
        domain.SideRemarkOverheard(
            speaker="uk", overheard_by="eu", clause_id=clause, suggests=suggests
        )
    )
    table.append(remark)
    table.believe(
        held("eu"),
        "witnessed",
        (table.seats["uk"], remark.envelope.event_id),
        True,
        uuid.uuid7(),
    )
    return remark.envelope.event_id


def test_the_brief_carries_all_three_sources(table: Table):
    """
    A brief built from one source would read identically with or without a model.

    Asserting the three lines separately because that is what makes the next test
    meaningful: if the modelled line were missing, the agent could not act on it and
    the behavioural test would pass for the wrong reason.

    Two lines come from stored scopes and one is derived per call. The brief cannot
    tell them apart, which is the point — R28 changes where the belief lives, not what
    an agent can be told.
    """
    _set_up(table, believed="concede")

    brief = brief_for(table.view("eu"), "uk", "fisheries")

    assert "Our position: settle" in brief
    assert "Their stated position: never" in brief
    assert "What we believe they actually want: concede" in brief


def test_the_offer_changes_when_only_the_derived_model_changes(table: Table):
    """
    The milestone's behavioural claim, as one comparison.

    Everything held fixed but our belief about them, and the agent proposes differently.

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

    fisheries = _set_up(table, believed="concede")
    hopeful = Negotiator(
        FunctionModel(read_the_brief), PinLedger(table.store, table.types), view=table.view("eu")
    ).propose("uk", "fisheries")

    # Same table, same stated position. A later remark the EU also believes the UK made
    # supersedes the first in the fold -- no row is edited, because there is no row.
    _leak(table, clause=fisheries, suggests="hold_firm")
    table.projector.catch_up()
    wary = Negotiator(
        FunctionModel(read_the_brief), PinLedger(table.store, table.types), view=table.view("eu")
    ).propose("uk", "fisheries")

    assert hopeful.concede is True
    assert wary.concede is False
    assert all("Their stated position: never" in prompt for prompt in seen), (
        "the public position never changed -- only what we believed about them"
    )


def _concluded(table: Table, *, about_them: str) -> None:
    """The EU's update call has concluded ``about_them`` is the UK's bottom line."""
    premise = record_offer(
        table,
        Offer(clause="fisheries", concede=False, because="opening"),
        party="uk",
        clause="fisheries",
    )
    record_reading(
        table,
        Reading(
            about="uk",
            clause="fisheries",
            predicate="red_line",
            value=about_them,
            because="what their opening told us",
        ),
        party="eu",
        premise=premise,
    )
    table.projector.catch_up()


def test_the_brief_carries_what_the_update_call_concluded(table: Table):
    """
    The fourth line: what this party concluded about the other last round.

    **It was missing, and nothing noticed.** M8b added a second pinned call per party
    per round, writing a ``red_line`` reading into the party's own scope, and nothing
    read it back: not the brief, not ``misreading``, not ``own_gap``. The update call
    ran, was pinned, replayed perfectly, and could not affect a single offer. Swapping
    its model for a better one or a worse one would have changed nothing measurable,
    and an evaluation would have scored every model an equally good updater.
    """
    _set_up(table, believed="hold_firm")
    _concluded(table, about_them="never")

    brief = brief_for(table.view("eu"), "uk", "fisheries")

    assert "Our read of their bottom line: never" in brief


def test_the_offer_changes_when_only_the_reading_changes(table: Table):
    """
    M7's behavioural test, for the update call instead of the derived model.

    Everything held fixed — what we want, what they said, what we derive they want —
    except what we concluded about them last round, and the demo's own proposer offers
    differently. That is what makes the update role's model worth choosing: until now
    nothing it produced could reach a decision.
    """
    _set_up(table, believed="hold_firm")
    _concluded(table, about_them="hold_firm")
    steady = Negotiator(
        scripted(), PinLedger(table.store, table.types), view=table.view("eu")
    ).propose("uk", "fisheries")

    _concluded(table, about_them="never")
    moved = Negotiator(
        scripted(), PinLedger(table.store, table.types), view=table.view("eu")
    ).propose("uk", "fisheries")

    assert steady.concede is False
    assert moved.concede is True, "a party read as immovable is one worth conceding to"
    assert "bottom line as never" in moved.because, "the reason names what moved it"


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
    live = Negotiator(FunctionModel(reply), ledger, view=table.view("eu")).propose(
        "uk", "fisheries"
    )
    ledger.commit()

    replay = ReplayPins.from_events(list(table.store.read()), table.types, on_miss="strict")
    again = Negotiator(FunctionModel(reply), replay, view=table.view("eu")).propose(
        "uk", "fisheries"
    )

    assert again == live
    assert len(calls) == 1, "the replayed round reached no model"


def test_text_output_is_not_what_the_agent_returns(table: Table):
    """Structured output, so a round's result is data the next round can act on."""

    def prose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I think we should concede, probably")])

    _set_up(table, believed="concede")
    negotiator = Negotiator(
        FunctionModel(prose), PinLedger(table.store, table.types), view=table.view("eu")
    )

    import pytest
    from pydantic_ai import UnexpectedModelBehavior

    with pytest.raises(UnexpectedModelBehavior):
        negotiator.propose("uk", "fisheries")
