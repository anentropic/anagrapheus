"""
Milestone-8 acceptance: two agents, four rounds, and a mind read wrongly.

M7 shipped one agent, called once, always for the EU, against a counterpart that was a
row in a database. Three of the domain's four events were defined and never emitted;
`pinned_tool` was used nowhere and the chronicle had no consumer at all. This is the
version where all of that is load-bearing.

**What no earlier milestone could assert.** NVM **R28 (RATIFIED 2026-06-17)** rules that
*"no `BeliefState` ever stores 'X believes Y believes …'"*, and M8a made storing one
impossible. That is a negative claim, and a negative claim proves nothing about whether
the positive one works. This is the positive one: the negotiation turns on a party that
is **right about the world and wrong about another mind**, and it is wrong at a level no
first-order record anywhere can express.

The UK lets something slip in round one and believes it went unheard. It did not go
unheard, and it goes on believing that for the rest of the negotiation. Round one is
where the two levels are measurably apart: the EU's model of the UK reads ``concede``
while the UK's model of *that model* reads ``hold_firm``. The gap closes in round two,
when the UK states its position publicly and last-evidence-wins re-buries the leak — so
what persists is the false **access-belief**, not the measured mismatch, and the test
below asserts exactly that pair rather than the stronger thing it is tempting to say.

A *stored* nested record could not even open that gap for one round, which is what
[doc 24 §1](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/24-nvm-higher-order-beliefs.md)
says a *stored* nested belief cannot do: it would be folded forward on the next world
event and quietly converge on the truth, *"and deception/surprise silently break."*

**And the whole thing replays with no model reached — on a fresh store.** Not the one it
recorded into: a different database, built by running the same story again, with every
draw answered from the first run's log. Sixteen agent calls and four consolidations —
twenty-eight model requests once the tool loop's continuations are counted — all pinned,
all reused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doxastica import BeliefFilter
from examples.negotiation.scopes import held, stated
from examples.negotiation.scripted import scripted
from examples.negotiation.story import CLAUSE, negotiate

from anagrapheus.chronicle import Anchors
from anagrapheus.timeline import ReplayPins

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from examples.negotiation.table import Table


def test_milestone_8_acceptance(tmp_path: Path):
    calls: list[str] = []
    run = negotiate(tmp_path / "summit.db", models=scripted(calls.append))
    table = run.table

    # -- four rounds, and the last one settles ----------------------------------------
    assert [r.number for r in run.rounds] == [1, 2, 3, 4]
    assert not run.rounds[0].turn("uk").offer.concede, "the UK opens by holding firm"
    assert run.rounds[3].turn("uk").offer.concede, (
        "and settles in round four, having overheard that the EU will not move"
    )
    assert not run.rounds[3].turn("eu").offer.concede, (
        "while the EU -- which has known since round one that the UK would move -- holds"
    )

    # -- the second-order error, which is the milestone --------------------------------
    #
    # Round one: the EU's model of the UK says `concede`, because the EU believes it
    # overheard the slip. The UK's model of *that model* says `hold_firm`, because the UK
    # believes nobody heard. Both are derived from the same log; they differ because they
    # are gated by different beliefs about access (R27), and neither was ever stored.
    first = run.second_order[0]
    assert (first.truly, first.imagined) == ("concede", "hold_firm")
    assert first.wrong, "the UK is mistaken about what the EU has concluded"
    assert not run.second_order[1].wrong, (
        "and the gap closes once the UK states the position publicly -- the model is "
        "last-evidence-wins, so a statement re-buries a leak"
    )

    # A first-order record cannot express that gap, which is why it is derived. What
    # *is* first-order is the distance between what a party says and what it wants, and
    # watching it close is what the as-of read is for: at round one's close the UK is
    # saying one thing and wanting another; by the end it has conceded and the gap is
    # gone. Neither reading says anything about what anybody *believes*.
    assert _gap_at(table, "uk", run.rounds[0].closed), "the UK opened by misrepresenting itself"
    assert not table.own_gap("uk"), "and closed the gap by conceding what it always wanted"
    assert not table.misreading("eu", "uk"), "the EU read them correctly throughout"

    # -- nothing nested was stored, anywhere -------------------------------------------
    #
    # Two scopes per party since M8a, and every belief in them minted from one of the
    # three declared predicates. There is no fourth scope and no fourth predicate, so
    # there is nowhere a nested belief could be.
    assert sorted(table.scopes()) == sorted([held("eu"), stated("eu"), held("uk"), stated("uk")])
    # By predicate name rather than by whole belief id: the id carries a version and a
    # namespace the registry owns, and asserting on its rendering would make this test
    # fail on a formatting change while saying nothing about nesting.
    used = {
        state.belief_id.rsplit(":", 1)[-1].split("(")[0]
        for scope in table.scopes()
        for state in table.core.query_scope(scope, BeliefFilter())
    }
    assert used == {"position", "red_line", "witnessed"}, (
        "every stored belief is one of the three declared first-order predicates, and "
        "all three are used -- there is no predicate that could express nesting"
    )

    # -- the chronicle consolidated each round -----------------------------------------
    anchors = list(Anchors(table.chronicle).all())
    assert len(anchors) == 4, "one section per `RoundClosed` -- note 05's first consumer"
    assert all(section.pins for section in table.chronicle.sections()), (
        "each drew through the ledger, so the sections can be shown to replay"
    )

    # -- a conclusion knows what it rests on -------------------------------------------
    opening = run.rounds[0].turn("eu").said
    compromised = table.core.get_impact(opening.state_id)
    assert [s.state_id for s in compromised.reached] == [
        run.rounds[0].turn("uk").concluded.state_id
    ], "withdraw the EU's opening and the UK's reading of it is what needs revising"

    # -- and per-round history, which the log makes free --------------------------------
    at_round_one = table.core.get_scope_at(held("uk"), run.rounds[0].closed)
    assert len(at_round_one) < len(table.core.query_scope(held("uk"), BeliefFilter())), (
        "the UK believed less at the first round's close than it does now"
    )

    run.commit()
    events = list(table.store.read())
    drawn = len(calls)
    assert drawn > 0, "the live run reached the model"
    table.close()

    # -- the same story, a fresh store, and nothing reaches a model ---------------------
    replayed = negotiate(
        tmp_path / "replay.db",
        models=scripted(calls.append),
        resolver=lambda t: ReplayPins.from_events(events, t.types, on_miss="strict"),
    )
    try:
        assert len(calls) == drawn, "the replayed negotiation reached no model at all"
        assert isinstance(replayed.pins, ReplayPins)
        assert replayed.pins.divergence is None
        assert [(t.party, t.offer.concede) for r in replayed.rounds for t in r.turns] == [
            (t.party, t.offer.concede) for r in run.rounds for t in r.turns
        ]
        assert replayed.second_order == run.second_order
    finally:
        replayed.table.close()


def _gap_at(table: Table, party: str, as_of: UUID) -> list[str]:
    """
    Where ``party`` said one thing and wanted another, as of an event.

    :func:`~anagrapheus.beliefs.divergence` compares two scopes *now* and there is no
    as-of form of it, so this is the same semantics over two ``get_scope_at`` reads:
    only beliefs both scopes hold, which is the library's rule and the right one — a
    belief one side never had is an absence rather than a contradiction.
    """
    wanted = {s.belief_id: s.value for s in table.core.get_scope_at(held(party), as_of)}
    said = {s.belief_id: s.value for s in table.core.get_scope_at(stated(party), as_of)}
    return sorted(b for b in wanted.keys() & said.keys() if wanted[b] != said[b])


def test_the_demo_is_what_is_tested(tmp_path: Path):
    """
    ``python -m examples.negotiation`` prints these steps, then what the replay cost.

    A demo that renders its own narration separately from what is asserted drifts within
    a milestone or two into describing behaviour the code no longer has. Both read the
    same object, so this is the assertion that keeps them one thing. The CLI adds one
    line of its own after the steps — what the strict replay proved — which is why this
    does not say "and nothing else", as it did until review.
    """
    run = negotiate(tmp_path / "printed.db", models=scripted())
    try:
        labels = [step.label for step in run.steps]
        assert labels[:3] == ["lineup", "tabled", "seeded"], (
            "the demo opens by saying which model filled each role -- what ran, not what "
            "was meant to"
        )
        assert run.steps[0].detail == run.lineup.describe(("eu", "uk"))
        assert labels.count("round 1 / depth 2") == 1
        assert all(step.detail for step in run.steps), "every step says something"
        assert f"{CLAUSE} is on the table" == run.steps[1].detail
    finally:
        run.table.close()
