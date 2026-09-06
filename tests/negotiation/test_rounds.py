"""
A round, and the four things it has to get right.

Simultaneity, the schema that forbids a nested belief, the access-beliefs a leak seeds,
and the justification edge. Each is a claim the demo makes, so each is asserted against
a mutant that would otherwise pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter
from examples.negotiation.agent import (
    Negotiator,
    Offer,
    Reading,
    UnrecordableReading,
    record_offer,
    record_reading,
)
from examples.negotiation.rounds import Leak, play_round
from examples.negotiation.scopes import held, stated
from examples.negotiation.scripted import scripted
from examples.negotiation.story import CLAUSE, RoundSummary
from examples.negotiation.table import Table
from pydantic import ValidationError

from anagrapheus.chronicle import Anchors
from anagrapheus.timeline import PinLedger, ReplayPins

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic_ai.models import Model


def _agents(
    table: Table, model: Model, ledger: PinLedger, *, archive: Callable[[str], str] | None = None
) -> dict[str, Negotiator]:
    return {
        party: Negotiator(model, ledger, view=table.view(party), archive=archive)
        for party in ("eu", "uk")
    }


def _opened(table: Table) -> None:
    """Give both sides something to want, and a clause to want it on."""
    clause = table.table_clause(CLAUSE)
    tabled = table.tabled_at[CLAUSE]
    table.believe(held("eu"), "position", (table.seats["eu"], clause), "settle", tabled)
    table.believe(held("uk"), "position", (table.seats["uk"], clause), "concede", tabled)
    table.projector.catch_up()


def _proposals(prompts: list[str]) -> list[str]:
    return [p for p in prompts if "propose whether to concede" in p]


def test_a_round_is_simultaneous(table: Table):
    """
    Neither side sees the other's offer inside the round it was made.

    The mutant this catches is the obvious implementation: record each offer as it comes
    in. Everything still runs, and the second party negotiates against a counterpart
    whose move it has already seen — which is not a round, and quietly deletes the
    uncertainty the whole demo is about.
    """
    _opened(table)
    prompts: list[str] = []
    ledger = PinLedger(table.store, table.types)
    play_round(table, _agents(table, scripted(prompts.append), ledger), clause=CLAUSE, number=1)

    briefs = _proposals(prompts)
    assert len(briefs) == 2, "one proposal per party"
    assert all("Their stated position: unknown" in brief for brief in briefs), (
        "the second party read an offer made in its own round"
    )


def test_a_round_refuses_a_third_party(table: Table):
    """A brief is pairwise, so a three-way round raises rather than guessing a shape."""
    _opened(table)
    table.seat("us")
    ledger = PinLedger(table.store, table.types)
    agents = _agents(table, scripted(), ledger)
    agents["us"] = Negotiator(scripted(), ledger, view=table.view("us"))

    with pytest.raises(ValueError, match="pairwise"):
        play_round(table, agents, clause=CLAUSE, number=1)


def test_a_reading_cannot_name_a_nested_predicate():
    """
    R28 is enforced by the output type, not by the prompt.

    There is no ``believes`` in the ``Literal``, so an update call that tried to record
    what the other party *believes* fails validation before anything is written. A
    prompt asking a model not to do something is a request; this is a wall.
    """
    with pytest.raises(ValidationError):
        Reading(
            about="uk",
            clause=CLAUSE,
            predicate="believes",  # type: ignore[arg-type]
            value="concede",
            because="they seemed to know",
        )


def test_a_reading_naming_nobody_raises_rather_than_keyerrors(table: Table):
    """A model naming a party that is not at the table is ordinary, and caught."""
    _opened(table)
    reading = Reading(
        about="norway", clause=CLAUSE, predicate="red_line", value="never", because="a guess"
    )
    with pytest.raises(UnrecordableReading, match="norway"):
        record_reading(table, reading, party="eu")


def test_a_conclusion_records_what_it_rests_on(table: Table):
    """
    Retracting the premise reports the conclusion that stood on it.

    ``add_edge(reading, premise, DERIVED_FROM)`` is the whole of it, and the direction
    matters: ``get_impact`` walks against the arrows. Reverse the two arguments and this
    test goes empty while everything else in the suite still passes.
    """
    _opened(table)
    premise = record_offer(
        table,
        Offer(clause=CLAUSE, concede=False, because="opening"),
        party="uk",
        clause=CLAUSE,
    )
    table.projector.catch_up()
    conclusion = record_reading(
        table,
        Reading(
            about="uk", clause=CLAUSE, predicate="red_line", value="never", because="they held"
        ),
        party="eu",
        premise=premise,
    )

    impact = table.core.get_impact(premise.state_id)
    assert [state.state_id for state in impact.reached] == [conclusion.state_id]


def test_a_conclusion_lands_in_the_concluding_party_own_scope(table: Table):
    """
    The EU concluding something about the UK does not write into the UK's mind.

    A belief about what the UK wants lives in the EU's head. Writing it into
    ``held("uk")`` instead reads almost identically — ``reading.about`` is right there,
    and it names the party the belief is *about* — and it is the EU editing its
    counterpart's private beliefs, which makes every subsequent ``misreading`` and
    ``own_gap`` a measurement of something the EU put there itself.

    **Written because that mutant survived the entire suite.** Swapping ``held(party)``
    for ``held(reading.about)`` left 338 tests green, including the M8 acceptance test
    with its four rounds and its second-order assertions. It is the fourth time in this
    example that a load-bearing claim turned out to have nothing holding it — note 06
    §2d records the first three, and the shape is always the same: the claim is stated
    in a docstring and asserted nowhere.
    """
    _opened(table)
    before = table.core.query_scope(held("uk"), BeliefFilter())
    state = record_reading(
        table,
        Reading(about="uk", clause=CLAUSE, predicate="red_line", value="never", because="a read"),
        party="eu",
    )

    assert state.scope_id == held("eu")
    assert table.core.query_scope(held("uk"), BeliefFilter()) == before, (
        "the UK's own scope is untouched by what the EU concluded about it"
    )


def test_a_leak_seeds_belief_about_access_and_not_the_truth(table: Table):
    """
    Access is staged, and a party absent from the staging believes nobody heard it.

    Including itself, which is the case that matters.

    Deriving ``believed_by`` from ``overheard_by`` would hand every party the truth and
    delete the deception R27 calls the engine. The mutant passes every other test here.
    """
    _opened(table)
    ledger = PinLedger(table.store, table.types)
    played = play_round(
        table,
        _agents(table, scripted(), ledger),
        clause=CLAUSE,
        number=1,
        leak=Leak(
            speaker="uk",
            overheard_by="eu",
            clause=CLAUSE,
            suggests="concede",
            believed_by={"eu": ("eu", "uk")},
        ),
    )
    assert played.leaked is not None

    eu = table.view("eu")
    uk = table.view("uk")
    remark = played.leaked
    assert eu.own_belief(eu.witnessed_id("uk", remark)) is not None, (
        "the EU believes the UK's own remark was heard by the UK"
    )
    assert uk.own_belief(uk.witnessed_id("uk", remark)) is None, (
        "the UK believes its slip went unheard -- the whole deception"
    )
    assert played.turn("uk").reading.about == "eu"


def test_an_offer_is_said_in_the_room_and_the_belief_cites_it(table: Table):
    """
    The ``stated`` belief's source event is the ``PositionStated`` that caused it.

    M7 wrote it against a bare ``uuid7()`` naming nothing, which reads identically and
    leaves the log with no record that anything was said — so a derived model, which
    can only gate on events, had nothing to gate on.
    """
    _opened(table)
    state = record_offer(
        table,
        Offer(clause=CLAUSE, concede=True, because="a gesture"),
        party="eu",
        clause=CLAUSE,
        heard_by=("eu", "uk"),
    )
    said = next(
        event
        for stored, event in table.reader.read()
        if stored.envelope.event_id == state.source_event_id
    )
    assert said.position == "concede"  # type: ignore[attr-defined]
    assert said.heard_by == ("eu", "uk")  # type: ignore[attr-defined]
    assert state.scope_id == stated("eu")


def test_a_round_closes_a_chronicle_unit(tmp_path: Path):
    """
    ``RoundClosed`` is the boundary, so one round consolidates to one section.

    M7 defined the event and never emitted it; note 05's fold had no consumer at all.
    Both halves of the reopened M7.3 land here.
    """
    summary = RoundSummary(scripted())
    table = Table.open(tmp_path / "rounds.db", "eu", "uk", summarize=summary)
    try:
        ledger = PinLedger(table.store, table.types)
        summary.draws_through(ledger)
        _opened(table)
        agents = _agents(table, scripted(), ledger)
        for number in (1, 2):
            play_round(table, agents, clause=CLAUSE, number=number)

        anchors = list(Anchors(table.chronicle).all())
        assert len(anchors) == 2, "one section per round, not one per event"
        # `unit` is the marker event's seq, and each anchor's range ends on it: a
        # section that stopped short of its own boundary would leave the round's last
        # events uncovered forever, since the next range starts after this one.
        assert [a.to_seq for a in anchors] == [a.unit for a in anchors]
        assert anchors[1].from_seq == anchors[0].to_seq + 1, "the ranges abut and do not overlap"
        assert all(section.prose for section in table.chronicle.sections())
        assert all(section.pins for section in table.chronicle.sections()), (
            "each consolidation drew through the ledger, so a rebuild can be shown to hit"
        )
    finally:
        table.close()


def test_the_archive_is_drawn_once_and_replayed(table: Table):
    """
    A pinned tool fires on the live run and not on the replay.

    Without it a replay reproduces what the agents *said* while re-running what they
    *did* — and the model half looks perfect throughout, which is why this needs its own
    assertion rather than riding on the call count.
    """
    _opened(table)
    reached: list[str] = []

    def archive(clause: str) -> str:
        reached.append(clause)
        return "settled 60/40 at the last summit"

    ledger = PinLedger(table.store, table.types)
    live = _agents(table, scripted(), ledger, archive=archive)["eu"].propose("uk", CLAUSE)
    assert reached == [CLAUSE], "the tool ran exactly once"
    ledger.commit()

    replay = ReplayPins.from_events(list(table.store.read()), table.types, on_miss="strict")
    again = Negotiator(scripted(), replay, view=table.view("eu"), archive=archive).propose(
        "uk", CLAUSE
    )

    assert again == live
    assert reached == [CLAUSE], "the replay reached the archive again"
    assert replay.divergence is None


def test_everything_said_is_believed_to_have_been_heard(table: Table):
    """
    A public offer seeds access-beliefs, and without them every derived model is empty.

    The gate admits only events the holder believes the target witnessed, so an offer
    nobody believes was witnessed is an offer no model can be built from — and the
    resulting empty model looks exactly like an ignorant party rather than like a bug.
    """
    _opened(table)
    ledger = PinLedger(table.store, table.types)
    play_round(table, _agents(table, scripted(), ledger), clause=CLAUSE, number=1)

    for holder in ("eu", "uk"):
        witnessed = [
            state
            for state in table.core.query_scope(held(holder), BeliefFilter())
            if ":witnessed(" in state.belief_id
        ]
        assert len(witnessed) == 4, "two offers, each believed heard by both parties"
    assert table.model_held_by("eu", "uk").beliefs, "and the model built from them is not empty"
