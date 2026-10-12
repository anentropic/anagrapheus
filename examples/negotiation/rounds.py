"""
A round: both sides act, both sides read, and the round closes.

This is the loop M7 never had. M7 ran one agent once, always for the EU, against a
counterpart that was a row in a database; three of the domain's four events were defined
and never emitted. What a round adds is not more calls — it is the thing four events and
two views were for.

**Simultaneity is the design decision here.** Every party proposes *before* any offer is
recorded. Record as you go and the second party reads the first's offer inside the same
round, which is not a round: it is two half-rounds, and the whole asymmetry the demo
exists to show — each side deciding under uncertainty about the other — collapses into
one side deciding under certainty. The cost is one extra pass over the parties. It buys
the only thing that makes the plural interesting.

**Five steps, and the middle three are the ones M7 could not reach:**

1. **Act.** Each party's :meth:`~examples.negotiation.agent.Negotiator.propose` — a
   pinned call, which may reach the archive through a pinned tool.
2. **Say.** Offers become ``PositionStated`` events and ``stated`` beliefs citing them.
3. **Leak.** Optionally a ``SideRemarkOverheard``, plus **what each party believes about
   who heard it** — staged, per R27, and free to be wrong. That divergence is the engine.
4. **Read.** Each party's :meth:`~examples.negotiation.agent.Negotiator.update` — the
   second pinned call — writes one first-order conclusion into its own scope, with a
   ``DERIVED_FROM`` edge to the offer it rests on.
5. **Close.** ``RoundClosed`` is appended, and the chronicle consolidates the round if a
   fold was installed.

**Two parties, and that is asserted rather than assumed.** A brief is pairwise: what we
want, what *they* said, what we think *they* want. With three parties a round has a
shape this module does not know — simultaneous pairwise briefs, or one offer to the
table — and guessing would ship a demo whose plural case had never been thought about.
It raises instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from examples.negotiation import domain
from examples.negotiation.agent import check_reading, record_offer, record_reading
from examples.negotiation.scopes import held

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from doxastica import BeliefState
    from examples.negotiation.agent import Negotiator, Offer, Reading
    from examples.negotiation.table import Table

__all__ = ["Leak", "Round", "Turn", "play_round"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Leak:
    """
    A remark let slip mid-round, and who each party *thinks* heard it.

    ``believed_by`` is deliberately not derived from ``overheard_by``. R27 makes access
    first-class observable content that actors form ordinary beliefs about, *"allowed to
    diverge from the true witness set — the divergence **is** the deception/surprise
    engine."* Deriving one from the other would hand every party the truth and delete
    the phenomenon, which is the same failure R29 prevents one layer up.

    So the staging is explicit: ``{"eu": ("eu", "uk"), "uk": ()}`` is a room where the EU
    believes both sides heard the remark and the UK believes nobody did. Doc 24 §1's
    guard and courier, in a negotiation's clothes.
    """

    speaker: str
    overheard_by: str
    clause: str
    #: How far the remark says the speaker will move, in the domain's vocabulary.
    suggests: domain.Disposition
    #: Holder -> the parties that holder believes witnessed the remark. A holder absent
    #: from this mapping believes nobody heard it, including itself.
    believed_by: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class Turn:
    """One party's round: what it offered, and what it concluded."""

    party: str
    offer: Offer
    reading: Reading
    #: The belief the offer minted, in this party's ``stated`` scope.
    said: BeliefState
    #: The belief the reading minted, in this party's own ``held`` scope. First-order.
    concluded: BeliefState


@dataclass(frozen=True, slots=True)
class Round:
    """A round played to its close."""

    number: int
    clause: str
    turns: tuple[Turn, ...]
    #: The ``RoundClosed`` event, which is the chronicle's unit boundary.
    closed: UUID
    #: The ``SideRemarkOverheard`` event, when one fired.
    leaked: UUID | None = None

    def turn(self, party: str) -> Turn:
        """One party's half of the round."""
        return next(t for t in self.turns if t.party == party)


def play_round(
    table: Table,
    agents: Mapping[str, Negotiator],
    *,
    clause: str,
    number: int,
    leak: Leak | None = None,
) -> Round:
    """
    Play one round to its close and return what happened.

    Four model calls for two parties — propose and update, each pinned — so the round
    replays offline in full. Raises if the model names a party or clause that is not at
    the table: see :class:`~examples.negotiation.agent.UnrecordableReading` for why that
    is a raise rather than a shrug.
    """
    if len(agents) != 2:
        raise ValueError(
            f"a round is pairwise and {len(agents)} parties were given: with three the "
            f"round has a shape this module has not decided on. See the module docstring."
        )
    opponents = {party: next(p for p in agents if p != party) for party in agents}

    # -- 1. act: every party proposes before any offer is recorded ----------------------
    _log.info("round %d: act -- each party proposes, unseen by the other", number)
    offers = {party: agent.propose(opponents[party], clause) for party, agent in agents.items()}
    for party, offer in offers.items():
        _log.info(
            "round %d: %s offers to %s (%s)",
            number,
            party,
            "concede" if offer.concede else "hold firm",
            offer.because,
        )

    # -- 2. say ------------------------------------------------------------------------
    _log.info("round %d: say -- offers are stated in the room", number)
    said = {
        # Everyone at the table is in the room, which is what "stated" means here.
        party: record_offer(table, offer, party=party, clause=clause, heard_by=tuple(agents))
        for party, offer in offers.items()
    }

    # -- 3. leak -----------------------------------------------------------------------
    if leak is not None:
        _log.info(
            "round %d: leak -- %s lets slip %r, overheard by %s",
            number,
            leak.speaker,
            leak.suggests,
            leak.overheard_by,
        )
    leaked = None if leak is None else _let_slip(table, leak)

    # Every belief above is already folded -- `Table.append` settles the pure folds --
    # but the chronicle is not, and the update call below reads the story so far.
    table.projector.catch_up()

    # -- 4. read -----------------------------------------------------------------------
    _log.info("round %d: read -- each party concludes something about the other", number)
    readings = {
        party: agent.update(
            opponents[party], clause, observed=_observed(table, party, offers, leak)
        )
        for party, agent in agents.items()
    }
    # Validate every party's output before writing any of it. Checking inside the write
    # loop leaves the first party's conclusion persisted when the second's is rejected,
    # and a round that never closes -- a half-written round is worse than a rejected
    # one, because the log then says something happened that did not.
    for party, reading in readings.items():
        check_reading(table, reading, about=opponents[party], clause=clause)
        _log.info(
            "round %d: %s concludes %s(%s) = %s (%s)",
            number,
            party,
            reading.predicate,
            reading.about,
            reading.value,
            reading.because,
        )
    concluded = {
        # The premise is what the *other* side said this round, which is the observation
        # the conclusion rests on. Retract it -- a bluff, withdrawn -- and `get_impact`
        # names this reading as compromised rather than leaving it standing.
        party: record_reading(table, reading, party=party, premise=said[opponents[party]])
        for party, reading in readings.items()
    }

    # -- 5. close ----------------------------------------------------------------------
    _log.info("round %d: close", number)
    closing = table.types.pending(domain.RoundClosed(round_number=number))
    table.append(closing)
    table.projector.catch_up()

    return Round(
        number=number,
        clause=clause,
        turns=tuple(
            Turn(
                party=party,
                offer=offers[party],
                reading=readings[party],
                said=said[party],
                concluded=concluded[party],
            )
            for party in agents
        ),
        closed=closing.envelope.event_id,
        leaked=leaked,
    )


def _let_slip(table: Table, leak: Leak) -> UUID:
    """
    Append the remark, then seed what each party believes about who heard it.

    Two writes, and the order matters: the access-beliefs cite the remark, so it has to
    exist first. They are ordinary first-order beliefs in each holder's own scope (R27),
    and nothing here consults ``overheard_by`` to decide them.
    """
    remark = table.types.pending(
        domain.SideRemarkOverheard(
            speaker=leak.speaker,
            overheard_by=leak.overheard_by,
            clause_id=table.clauses[leak.clause],
            suggests=leak.suggests,
        )
    )
    table.append(remark)
    event_id = remark.envelope.event_id
    for holder, witnesses in leak.believed_by.items():
        for witness in witnesses:
            table.believe(
                held(holder),
                "witnessed",
                (table.canonical(table.seats[witness]), event_id),
                True,
                event_id,
            )
    return event_id


def _observed(table: Table, party: str, offers: Mapping[str, Offer], leak: Leak | None) -> str:
    """
    What ``party`` saw of this round, in its own terms.

    Offers are public — they were said in the room — so every party sees all of them.
    The remark is not: a party reads it only if it believes *itself* to have heard it,
    which is a belief and may be wrong in either direction. Handing every party the
    remark because it is in the log would be the log-is-the-truth mistake that
    :meth:`~examples.negotiation.view.PartyView.log` already refuses one layer down.
    """
    lines = [
        f"{name} offered: {'concede' if offer.concede else 'hold firm'} ({offer.because})"
        for name, offer in offers.items()
    ]
    if leak is not None and party in leak.believed_by.get(party, ()):
        lines.append(
            f"You overheard {leak.speaker} let slip that they would "
            f"{leak.suggests} on {leak.clause}."
        )
    return "\n".join(lines)
