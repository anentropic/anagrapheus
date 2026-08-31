"""
An agent negotiating for one party, and reading its model of another.

The demo's provider half. What makes it worth writing is not that it calls a model —
tiers 1 and 2 already prove that path — but *what it is given*: a brief assembled from
a **model-of scope**, so the concession it proposes depends on what its party believes
the other side actually wants, not on what the other side said.

That is the assertion the acceptance test makes: change only
``party:eu:model-of:uk`` and the agent's offer changes, with every other input held
fixed. A single-holder design has nowhere to make that change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from examples.negotiation.scopes import held, model_of, stated
from pydantic import BaseModel
from pydantic_ai import Agent

from anagrapheus.provider import PinnedModel

if TYPE_CHECKING:
    from uuid import UUID

    from examples.negotiation.table import Table
    from pydantic_ai.models import Model

    from anagrapheus.timeline.pins import PinResolver

__all__ = ["Offer", "Negotiator", "brief_for"]


class Offer(BaseModel):
    """What the agent proposes, and the belief it rests on."""

    clause: str
    concede: bool
    because: str


def brief_for(table: Table, holder: str, other: str, clause: str) -> str:
    """
    What ``holder`` knows going into a round about ``other``'s position on ``clause``.

    Three lines from three scopes, and the difference between them is the whole brief:
    what we want, what they said, and what we think they actually want. A brief built
    from one scope would read identically whether or not we had any model of them.
    """
    from doxastica import BeliefFilter

    def one(scope: str) -> str:
        states = list(table.core.query_scope(scope, BeliefFilter()))
        return str(states[0].value) if states else "unknown"

    return (
        f"Clause: {clause}\n"
        f"Our position: {one(held(holder))}\n"
        f"Their stated position: {one(stated(other))}\n"
        f"What we believe they actually want: {one(model_of(holder, other))}"
    )


class Negotiator:
    """One party's agent. Pinned, so a round replays without reaching a model."""

    def __init__(self, model: Model, resolver: PinResolver, *, party: str) -> None:
        self._agent = Agent(model=PinnedModel(model, resolver), output_type=Offer)
        self._party = party

    def propose(self, table: Table, other: str, clause: str) -> Offer:
        """Read the brief and offer a move. The model call is pinned like any other."""
        return self._agent.run_sync(
            "You are negotiating for "
            f"{self._party}. Given the brief, propose whether to concede this clause.\n\n"
            + brief_for(table, self._party, other, clause)
        ).output


def record_offer(table: Table, offer: Offer, *, party: str, clause_id: UUID, source: UUID) -> None:
    """Put the offer into the party's stated scope — what it has now said in the room."""
    table.believe(
        stated(party),
        "position",
        (table.seats[party], clause_id),
        "concede" if offer.concede else "hold_firm",
        source,
    )
