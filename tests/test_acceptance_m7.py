"""
Milestone-7 acceptance: a second consumer, and the case one believer cannot state.

The design asks for a demo agent as *"the second consumer that tests whether these
seams are real; the less it resembles NVM, the more it proves."* This is that consumer:
a multilateral negotiation, with no fiction, no player and no narration anywhere in it.

The story: two parties, one clause, and a round in which the EU proposes a move based
on what it believes the UK actually wants — which is not what the UK said. Then a
delegate name turns out to be the same party under another name, the merge cascades
across every scope, and the whole run replays from the log with no model reached.

**What no earlier milestone could assert**: beliefs are kept per scope so *"several
holders can believe contradictory things at once and where they diverge is a query"*,
and that query is *"the whole multi-agent-disagreement case"*. Every use of it
in this repository until now was one believer against ground truth.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from examples.negotiation import domain
from examples.negotiation.agent import Negotiator, record_offer
from examples.negotiation.rekeying import apply_rekeys
from examples.negotiation.scopes import held, stated
from examples.negotiation.table import Table
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.beliefs import BeliefIndex, plan_rekeys
from anagrapheus.identity import merge
from anagrapheus.timeline import PinLedger, ReplayPins

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


def test_milestone_7_acceptance(tmp_path: Path):
    table = Table.open(tmp_path / "summit.db", "eu", "uk")
    index = table.index_store

    fisheries = table.table_clause("fisheries")
    # The UK is seated twice: the delegation and the government, not yet known to be one.
    uk, hmg = table.seats["uk"], table.seat("HMG")
    eu = table.seats["eu"]
    source = uuid.uuid7()

    # -- three holders, three positions on one proposition ----------------------------
    table.believe(held("eu"), "position", (eu, fisheries), "settle", source)
    table.believe(held("uk"), "position", (hmg, fisheries), "concede", source)
    table.believe(stated("uk"), "position", (hmg, fisheries), "never", source)

    # What the EU thinks the UK wants is **not** a fourth row. Higher-order belief is
    # derived, never stored, so it is an overheard remark plus the EU's belief that the UK let
    # it slip -- and the model is rebuilt from those every time it is asked for.
    leak = table.types.pending(
        domain.SideRemarkOverheard(
            speaker="HMG", overheard_by="eu", clause_id=fisheries, suggests="concede"
        )
    )
    table.append(leak)
    table.believe(held("eu"), "witnessed", (hmg, leak.envelope.event_id), True, source)
    table.projector.catch_up()

    # -- the delegations turn out to be one party -------------------------------------
    # Before the round, deliberately: the brief looks a position up by its exact belief
    # id, so until the delegation and the government are known to be one party there is
    # no `position(uk, fisheries)` to find. Reconciling identity is a precondition for
    # briefing an agent, not a tidy-up afterwards.
    table.append(merge(table.types, kept=uk, absorbed=hmg))
    table.projector.catch_up()
    plans = plan_rekeys(table.identity.rekeys_since(0), BeliefIndex(index), table.predicates)
    applied = apply_rekeys(table, plans, source_event=uuid.uuid7())
    table.projector.catch_up()

    assert [a.scope for a in applied] == [
        held("eu"),
        held("uk"),
        stated("uk"),
    ], "three scopes -- the plan cannot say where, so the application does"

    # -- and only now can the three questions be asked ---------------------------------
    #
    # They come after the merge, and they have to. M7 already learned half of this --
    # "reconciling identity is a precondition for briefing an agent, not a tidy-up
    # afterwards" -- and M8 extends it to the queries themselves. A derived model binds
    # a party *name* to an entity through `seats`, so while the delegation and the
    # government are two entities, "the UK" names one of them and the EU's beliefs are
    # about the other. Asking before the merge is not wrong; it is a question about a
    # party that does not exist yet, and it correctly answers nothing.
    #
    # M7 could ask early only because both sides of every `divergence` were stored
    # scopes keyed on the same absorbed entity, so the scope *name* and the entity were
    # decoupled and the inconsistency stayed invisible.

    # The queries still answer, which is what makes the cascade correct rather than
    # merely complete: a partial re-key would report perfect agreement.
    # The three questions, and they are three different questions.
    assert [(d.held, d.reference) for d in table.own_gap("uk")] == [("never", "concede")]
    assert table.misreading("eu", "uk") == [], "the EU happens to have read them right"
    assert [(d.held, d.reference) for d in table.taken_at_their_word("eu", "uk")] == [
        ("concede", "never")
    ], "and is right not to take them at their word"

    # And the derived model still answers, which it does for a *different* reason and
    # that difference is the M8 finding. The re-key moved the stored beliefs. It could
    # not move the remark: the log is immutable, and that event still says `HMG`. What
    # keeps the model aligned is the interpreter resolving every party reference through
    # the merge chain at read time. A stored model of another mind is carried by the
    # re-key; a derived one has to canonicalize itself.
    assert (
        table.model_held_by("eu", "uk").get(table.predicates.belief_id("position", uk, fisheries))
        == "concede"
    ), "same event, same log, and now keyed on the survivor"

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
    offer = Negotiator(FunctionModel(negotiate), ledger, view=table.view("eu")).propose(
        "uk", "fisheries"
    )
    assert offer.concede is True
    assert "Their stated position: never" in calls[0], (
        "the agent saw the public position and moved on the private one anyway"
    )
    record_offer(table, offer, party="eu", clause="fisheries")
    ledger.commit()

    # -- and the round replays with no model reached ----------------------------------
    events = list(table.store.read())
    drawn = len(calls)
    replay = ReplayPins.from_events(events, table.types, on_miss="strict")

    again = Negotiator(FunctionModel(negotiate), replay, view=table.view("eu")).propose(
        "uk", "fisheries"
    )

    assert again == offer
    assert len(calls) == drawn, "the replayed round reached no model"
    assert replay.divergence is None
    table.close()
