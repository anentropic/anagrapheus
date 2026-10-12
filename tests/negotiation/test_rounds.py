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
    UnrecordableOutput,
    record_offer,
    record_reading,
)
from examples.negotiation.rounds import Leak, play_round
from examples.negotiation.scopes import held, stated
from examples.negotiation.scripted import respond, scripted
from examples.negotiation.story import CLAUSE, RoundSummary
from examples.negotiation.table import Table
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.chronicle import Anchors
from anagrapheus.timeline import PinLedger, ReplayPins

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo


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


def test_a_reading_can_only_conclude_what_the_brief_reads():
    """
    The update call may write only ``red_line``, because it is the only one read back.

    **Found by the first real model to drive the harness.** The schema also allowed
    ``position``, and the update prompt asked "what {other} actually wants" -- which *is*
    a position -- while the brief read a bottom line. The scripted model always chose
    ``red_line``, so nothing noticed; the real one chose ``position`` for seven of its
    eight conclusions, and every one was stored and read by nothing. The gap the fourth
    brief line closed, reopened through the predicate beside it.
    """
    with pytest.raises(ValidationError):
        Reading(
            about="uk",
            clause=CLAUSE,
            predicate="position",  # type: ignore[arg-type]
            value="concede",
            because="what they want, which nothing would read",
        )


def test_a_reading_naming_nobody_raises_rather_than_keyerrors(table: Table):
    """A model naming a party that is not at the table is ordinary, and caught."""
    _opened(table)
    premise = record_offer(
        table, Offer(clause=CLAUSE, concede=False, because="opening"), party="uk", clause=CLAUSE
    )
    reading = Reading(
        about="norway", clause=CLAUSE, predicate="red_line", value="never", because="a guess"
    )
    with pytest.raises(UnrecordableOutput, match="norway"):
        record_reading(table, reading, party="eu", premise=premise)


def test_an_offer_on_another_clause_is_refused(table: Table):
    """
    ``offer.clause`` is checked against the clause being negotiated.

    Review finding. It was ignored entirely: the event and the belief took the round's
    clause, so a model proposing on something else had its answer filed under this one
    and the returned ``Offer`` disagreed with the ``PositionStated`` written for it.
    Nothing compared them, so nothing failed.
    """
    _opened(table)
    table.table_clause("quotas")
    with pytest.raises(UnrecordableOutput, match="quotas"):
        record_offer(
            table,
            Offer(clause="quotas", concede=True, because="wrong clause"),
            party="eu",
            clause=CLAUSE,
        )


def test_a_conclusion_cites_a_real_event(table: Table):
    """
    A belief's ``source_event_id`` names an event that is actually in the log.

    Review finding: ``record_reading``'s premise used to be optional and the fallback
    minted a bare ``uuid7()``. :mod:`anagrapheus.beliefs.writer` is explicit that the
    envelope id is the handle worth supplying — it makes "which event caused this
    belief" answerable, and because doxastica orders states by ``source_event_id`` in
    UUID7 byte order it makes the as-of cut agree with the log's own order. A phantom
    id answers with an event that never happened and sorts wherever it lands.

    **This test does not kill that mutant, and saying so is the point.** Restoring the
    optional parameter and the fallback leaves the whole suite green, because the round
    loop always passed a premise and the phantom path was only ever reachable by a
    caller that did not yet exist. So the fix is not this assertion — it is making the
    parameter required, which puts the bad call out of reach of the type system rather
    than merely out of reach of the tests. What this asserts is the *invariant* the fix
    protects, over every belief the round writes, which is worth having on its own and
    is the thing a future caller would break.
    """
    _opened(table)
    ledger = PinLedger(table.store, table.types)
    played = play_round(table, _agents(table, scripted(), ledger), clause=CLAUSE, number=1)

    in_the_log = {stored.envelope.event_id for stored in table.store.read()}
    for turn in played.turns:
        assert turn.concluded.source_event_id in in_the_log, (
            "a conclusion cited an event that is nowhere in the log"
        )
        assert turn.said.source_event_id in in_the_log


def test_no_conclusion_is_written_when_another_party_output_is_invalid(table: Table):
    """
    A round validates every party's output before writing any of it.

    Review finding. Recording inside the loop left the first party's conclusion
    persisted when the second's was rejected — and the round never closed, so the log
    said something had happened that had not. A half-written round is worse than a
    rejected one.

    Since the validator sends a misnamed party back to the model, a model has to stay
    wrong through the retry to get here; the round then fails on the retry limit, and
    still before it writes any conclusion.
    """
    _opened(table)
    ledger = PinLedger(table.store, table.types)

    def astray(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """The UK's update names a party that is not at the table, every time."""
        prompt = str(messages[0].parts[-1].content)  # type: ignore[union-attr]
        if "negotiating for uk" in prompt and "Record one conclusion" in prompt:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "final_result",
                        {
                            "about": "norway",
                            "clause": CLAUSE,
                            "predicate": "red_line",
                            "value": "never",
                            "because": "a party that is not here",
                        },
                    )
                ]
            )
        return respond(messages, info)

    agents = {
        party: Negotiator(FunctionModel(astray), ledger, view=table.view(party))
        for party in ("eu", "uk")
    }
    before = {scope: len(table.core.query_scope(scope, BeliefFilter())) for scope in table.scopes()}

    with pytest.raises(UnexpectedModelBehavior, match="retries"):
        play_round(table, agents, clause=CLAUSE, number=1)

    after = {scope: len(table.core.query_scope(scope, BeliefFilter())) for scope in table.scopes()}
    concluded = {
        scope: len(
            [
                s
                for s in table.core.query_scope(scope, BeliefFilter())
                if ":red_line(" in s.belief_id
            ]
        )
        for scope in table.scopes()
    }
    assert concluded == dict.fromkeys(table.scopes(), 0), (
        "the EU's conclusion was written before the UK's was rejected"
    )
    assert after.keys() == before.keys()


def test_a_misnamed_clause_is_sent_back_and_the_retry_replays(table: Table):
    """
    An offer naming the clause wrongly is returned to the model, which tries again.

    Found live: the first Claude model to drive the demo wrote a sentence describing
    its offer where the clause's name goes, and the round died on the refusal. The
    retry is a model call like any other, so it is pinned; a replay that finishes in
    strict mode answered it from the log too.
    """
    _opened(table)
    told: list[str] = []

    def describes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Describe the offer in the clause field, until told what goes there."""
        retries = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        told.extend(str(part.content) for part in retries)
        named = CLAUSE if retries else "Fisheries: we hold at 60/40"
        args = {"clause": named, "concede": False, "because": "we hold"}
        return ModelResponse(parts=[ToolCallPart("final_result", args)])

    ledger = PinLedger(table.store, table.types)
    live = _agents(table, FunctionModel(describes), ledger)["eu"].propose("uk", CLAUSE)
    ledger.commit()

    assert live.clause == CLAUSE
    assert told, "the model was never told what was wrong"
    assert f"must be exactly {CLAUSE!r}" in told[0]

    replay = ReplayPins.from_events(list(table.store.read()), table.types, on_miss="strict")
    again = Negotiator(FunctionModel(describes), replay, view=table.view("eu")).propose(
        "uk", CLAUSE
    )
    assert again == live
    assert replay.divergence is None


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
    premise = record_offer(
        table, Offer(clause=CLAUSE, concede=False, because="opening"), party="uk", clause=CLAUSE
    )
    before = table.core.query_scope(held("uk"), BeliefFilter())
    state = record_reading(
        table,
        Reading(about="uk", clause=CLAUSE, predicate="red_line", value="never", because="a read"),
        party="eu",
        premise=premise,
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
    The ``stated`` belief was caused by the ``PositionStated`` it records.

    M7 wrote it against a bare ``uuid7()`` naming nothing, which reads identically and
    leaves the log with no record that anything was said — so a derived model, which
    can only gate on events, had nothing to gate on. Since note 03 §9 the state's own
    source is the belief event the fold read it from, and the ``PositionStated`` is that
    event's cause.
    """
    _opened(table)
    state = record_offer(
        table,
        Offer(clause=CLAUSE, concede=True, because="a gesture"),
        party="eu",
        clause=CLAUSE,
        heard_by=("eu", "uk"),
    )
    envelopes = {stored.envelope.event_id: stored.envelope for stored, _ in table.reader.read()}
    cause = envelopes[state.source_event_id].causation_id
    said = next(event for stored, event in table.reader.read() if stored.envelope.event_id == cause)
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
    assert reached == [CLAUSE], "the replay ran the archive lookup a second time"
    assert replay.divergence is None


def test_the_chronicle_never_carries_a_private_event_to_a_party(tmp_path: Path):
    """
    A shared projection cannot describe a private event, because everyone reads it.

    **Review finding, and the worst kind this PR could have had**: the chronicle was
    rendered from the omniscient log and appended to *both* agents' prompts, so from
    round two the UK's own brief read *"uk let slip they would concede, overheard by
    eu"* — the exact truth its access-belief denies, and the one thing
    :class:`~examples.negotiation.view.PartyView` raises rather than answer.

    R29 held on the belief path and was broken one layer below it, in the prompt. The
    scripted counterpart masked it completely: it reads the belief lines and ignores the
    prose, so every test passed while a live model was being handed the answer.

    The fix is a constraint on what may enter a *shared* generative projection at all.
    There is no vantage point from which one chronicle can summarize a private event
    for every reader, because a party that must not know it is one of the readers. So
    the chronicle is the minutes of the room: what was said, and nothing that was let
    slip.
    """
    summary = RoundSummary(scripted())
    table = Table.open(tmp_path / "leak.db", "eu", "uk", summarize=summary)
    try:
        ledger = PinLedger(table.store, table.types)
        summary.draws_through(ledger)
        _opened(table)
        prompts: list[str] = []
        agents = _agents(table, scripted(prompts.append), ledger)
        play_round(
            table,
            agents,
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
        play_round(table, agents, clause=CLAUSE, number=2)

        assert not any("let slip" in section.prose for section in table.chronicle.sections()), (
            "a section describing a remark reaches every party, the speaker included"
        )
        for prompt in prompts:
            if "negotiating for uk" in prompt:
                assert "let slip" not in prompt, (
                    "the UK was handed its own slip, which it believes went unheard"
                )
    finally:
        table.close()


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


def test_a_bottom_line_written_as_a_sentence_is_sent_back(table: Table):
    """
    A reading's value is one of three words, and a sentence is returned to the model.

    Found live: Claude Haiku 5.5 answered a bottom line with *"unknown: no confirmed
    furthest movement; 60/40 coastal-state split is a reference range, not a stated
    limit"*, and with ``value: str`` that sentence became a belief value -- read back
    into the next brief and comparable with nothing. The schema refuses it now; the
    nuance belongs in ``because``.
    """
    _opened(table)
    told: list[str] = []

    def hedges(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Answer in prose until told the value is a choice of three."""
        retries = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        told.extend(str(part.content) for part in retries)
        args = {
            "about": "uk",
            "clause": CLAUSE,
            "predicate": "red_line",
            "value": "hold_firm" if retries else "unknown: no confirmed furthest movement",
            "because": "they have not moved, and nothing says they cannot",
        }
        return ModelResponse(parts=[ToolCallPart("final_result", args)])

    ledger = PinLedger(table.store, table.types)
    reading = _agents(table, FunctionModel(hedges), ledger)["eu"].update(
        "uk", CLAUSE, observed="uk offered: hold firm"
    )

    assert reading.value == "hold_firm"
    assert told, "the model was never told what was wrong"
    assert "concede" in told[0] and "never" in told[0], "the retry names the choices"
