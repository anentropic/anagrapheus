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

from examples.negotiation.nesting import reconstruct
from examples.negotiation.scopes import stated
from pydantic import BaseModel
from pydantic_ai import Agent

from anagrapheus.provider import PinnedModel

if TYPE_CHECKING:
    from uuid import UUID

    from examples.negotiation.table import Table
    from examples.negotiation.view import PartyView
    from pydantic_ai.models import Model

    from anagrapheus.timeline.pins import PinResolver

__all__ = ["Offer", "Negotiator", "brief_for"]


class Offer(BaseModel):
    """What the agent proposes, and the belief it rests on."""

    clause: str
    concede: bool
    because: str


def brief_for(view: PartyView, other: str, clause: str) -> str:
    """
    What ``view``'s party knows going into a round about ``other``'s position.

    Three lines from three sources, and the difference between them is the whole brief:
    what we want, what they said, and what we think they actually want. A brief built
    from one source would read identically whether or not we had any model of them.

    **The third line is derived, not read.** Since M8 there is no
    ``party:{a}:model-of:{b}`` scope to query — R28 forbids storing it — so it is
    reconstructed from this party's access-beliefs at the moment the brief is assembled
    and discarded with the brief. Doc 24 calls that abduction on demand applied to
    minds: never materialise the whole tower of what everyone could think about
    everyone, reconstruct the one nested query actually asked.

    Everything here comes through the :class:`~examples.negotiation.view.PartyView`, so
    the brief physically cannot include the other side's private beliefs. That is R29
    holding by construction rather than by care — and it matters most here, because the
    brief is exactly where a peek would be invisible and devastating: an agent handed
    the truth about its counterpart looks like an agent that reads them well.

    Each line is looked up by its **exact belief id**. Taking the first belief in a
    scope reads correctly while a scope holds one thing and starts answering with an
    unrelated clause the moment it holds two, at which point the agent negotiates
    confidently about the wrong proposition — the kind of wrong that never raises. It
    has happened here once; note 06 §5b records it.
    """
    wanted = view.position_id(view.party, clause)
    theirs = view.position_id(other, clause)

    ours = view.own_belief(wanted)
    said = view.public_belief(other, theirs)
    modelled = reconstruct(
        view,
        (other,),
        interpret=view.interpret,
    ).get(theirs)

    return (
        f"Clause: {clause}\n"
        f"Our position: {'unknown' if ours is None else ours.value}\n"
        f"Their stated position: {'unknown' if said is None else said.value}\n"
        f"What we believe they actually want: {'unknown' if modelled is None else modelled}"
    )


class Negotiator:
    """
    One party's agent. Pinned, so a round replays without reaching a model.

    Constructed with a :class:`~examples.negotiation.view.PartyView` rather than the
    table. That is the whole of R29 on the reasoning path: an agent holding the table
    could read its counterpart's private scope in one line and would look better for
    it, so it is never handed one.
    """

    def __init__(self, model: Model, resolver: PinResolver, *, view: PartyView) -> None:
        self._agent = Agent(model=PinnedModel(model, resolver), output_type=Offer)
        self._view = view

    @property
    def party(self) -> str:
        """Who this agent negotiates for."""
        return self._view.party

    def propose(self, other: str, clause: str) -> Offer:
        """Read the brief and offer a move. The model call is pinned like any other."""
        return self._agent.run_sync(
            "You are negotiating for "
            f"{self.party}. Given the brief, propose whether to concede this clause.\n\n"
            + brief_for(self._view, other, clause)
        ).output


def record_offer(table: Table, offer: Offer, *, party: str, clause_id: UUID, source: UUID) -> None:
    """
    Put the offer into the party's stated scope — what it has now said in the room.

    Takes the table, not a view: writing is the composition root's job. A view is a
    read surface by construction, which is why an agent cannot record its own offer and
    the round loop has to do it — the same split that keeps `Table.believe`'s
    mint-and-append pairing in one place (note 06 §4).
    """
    table.believe(
        stated(party),
        "position",
        (table.seats[party], clause_id),
        "concede" if offer.concede else "hold_firm",
        source,
    )
