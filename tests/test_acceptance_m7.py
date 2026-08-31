"""
Milestone-7 acceptance: a second consumer, and the case one believer cannot state.

Doc 00 §10 Q4 asks for a demo agent as *"the second consumer that tests whether these
seams are real; the less it resembles NVM, the more it proves."* This is that consumer:
a multilateral negotiation, with no fiction, no player and no narration anywhere in it.

The story: two parties, one clause, and a round in which the EU proposes a move based
on what it believes the UK actually wants — which is not what the UK said. Then a
delegate name turns out to be the same party under another name, the merge cascades
across every scope, and the whole run replays from the log with no model reached.

**What no earlier milestone could assert**: §6 keeps beliefs per scope so *"several
holders can believe contradictory things at once and where they diverge is a query"*,
and §6c calls that query *"the whole multi-agent-disagreement case"*. Every use of it
in this repository until now was one believer against ground truth.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.beliefs import BeliefIndex, BeliefIndexFold, SQLiteBeliefIndexStore, plan_rekeys
from anagrapheus.identity import IdentityFold, merge
from anagrapheus.timeline import PinLedger, Projector, ReplayPins
from examples.negotiation.agent import Negotiator, record_offer
from examples.negotiation.rekeying import apply_rekeys
from examples.negotiation.scopes import held, model_of, stated
from examples.negotiation.table import Table

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


def test_milestone_7_acceptance(tmp_path: Path):
    table = Table.open(tmp_path / "summit.db", "eu", "uk")
    index = SQLiteBeliefIndexStore(table.store.connection)
    table.index_store = index
    table.projector = Projector(
        table.store,
        table.reader,
        [IdentityFold(table.identity, table.types), BeliefIndexFold(index, table.types)],
    )

    fisheries = table.table_clause("fisheries")
    # The UK is seated twice: the delegation and the government, not yet known to be one.
    uk, hmg = table.seats["uk"], table.seat("HMG")
    eu = table.seats["eu"]
    source = uuid.uuid7()

    # -- three holders, three positions on one proposition ----------------------------
    table.believe(held("eu"), "position", (eu, fisheries), "settle", source)
    table.believe(held("uk"), "position", (hmg, fisheries), "concede", source)
    table.believe(stated("uk"), "position", (hmg, fisheries), "never", source)
    table.believe(model_of("eu", "uk"), "position", (hmg, fisheries), "concede", source)
    table.projector.catch_up()

    # The three questions, and they are three different questions.
    assert [(d.held, d.reference) for d in table.own_gap("uk")] == [("never", "concede")]
    assert table.misreading("eu", "uk") == [], "the EU happens to have read them right"
    assert [(d.held, d.reference) for d in table.taken_at_their_word("eu", "uk")] == [
        ("concede", "never")
    ], "and is right not to take them at their word"

    # -- a round: the agent proposes from its model, not from what was said ------------
    calls: list[str] = []

    def negotiate(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = str(messages[-1].parts[-1].content)  # type: ignore[union-attr]
        calls.append(prompt)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result",
                    {
                        "clause": "fisheries",
                        "concede": "actually want: concede" in prompt,
                        "because": "they will move despite what they said",
                    },
                )
            ]
        )

    ledger = PinLedger(table.store, table.types)
    offer = Negotiator(FunctionModel(negotiate), ledger, party="eu").propose(
        table, "uk", "fisheries"
    )
    assert offer.concede is True
    assert "Their stated position: never" in calls[0], (
        "the agent saw the public position and moved on the private one anyway"
    )
    record_offer(table, offer, party="eu", clause_id=fisheries, source=uuid.uuid7())
    ledger.commit()

    # -- the delegations turn out to be one party -------------------------------------
    table.append(merge(table.types, kept=uk, absorbed=hmg))
    table.projector.catch_up()
    plans = plan_rekeys(table.identity.rekeys_since(0), BeliefIndex(index), table.predicates)
    applied = apply_rekeys(table, plans, source_event=uuid.uuid7())

    assert len(plans) == 1, "one belief id"
    assert [a.scope for a in applied] == [
        held("uk"),
        stated("uk"),
        model_of("eu", "uk"),
    ], "three scopes -- the plan cannot say where, so the application does"

    # The queries still answer, which is what makes the cascade correct rather than
    # merely complete: a partial re-key would report perfect agreement.
    assert [(d.held, d.reference) for d in table.own_gap("uk")] == [("never", "concede")]

    # -- and the round replays with no model reached ----------------------------------
    events = list(table.store.read())
    drawn = len(calls)
    replay = ReplayPins.from_events(events, table.types, on_miss="strict")

    again = Negotiator(FunctionModel(negotiate), replay, party="eu").propose(
        table, "uk", "fisheries"
    )

    assert again == offer
    assert len(calls) == drawn, "the replayed round reached no model"
    assert replay.divergence is None
    table.close()
