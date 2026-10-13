"""
What each of the demo's model calls is shown, checked by its sources rather than its words.

Since M9 every call assembles its prompt from a grant (note 07), so the context knows
every belief, event and section it was built from. That turns the demo's worst prompt
defect into an assertion: a private remark reached the speaker's own prompt through the
chronicle's shared prose, and nothing failed, because the scripted counterpart ignored
the prose (note 06 §9a). These tests would have failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from examples.negotiation.agent import proposing, reading, render_proposing
from examples.negotiation.domain import SideRemarkOverheard
from examples.negotiation.scopes import held
from examples.negotiation.story import PARTIES, summarising

from anagrapheus.assembly import EventRef, Register, SectionRef, StaleRead
from anagrapheus.beliefs import StateRef
from tests.negotiation.test_rebuild import _replayed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from examples.negotiation.table import Table

    from anagrapheus.assembly import Context


@pytest.fixture(scope="module")
def table(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Table]:
    table = _replayed(tmp_path_factory.mktemp("assembly") / "negotiation.db")
    yield table
    table.close()


def _scopes(context: Context) -> set[str]:
    return {s.scope_id for s in context.read if isinstance(s, StateRef)}


@pytest.mark.parametrize("party", PARTIES)
def test_a_party_is_shown_no_belief_from_another_partys_private_scope(table: Table, party: str):
    """R29, asserted on what the prompt was built from rather than on what it says."""
    other = next(p for p in PARTIES if p != party)
    clause = next(iter(table.clauses))
    for context in (
        proposing(table.view(party).settled(), other, clause),
        reading(table.view(party).settled(), other, clause, observed="eu offered: hold firm"),
    ):
        scopes = _scopes(context)
        assert scopes, "the brief read beliefs, so the comparison is not vacuous"
        assert held(other) not in scopes
        assert all(s == held(party) or s.endswith(":stated") for s in scopes)


def test_the_summarizer_is_never_shown_a_private_remark(table: Table):
    """
    The chronicle's prose goes to every party, so it may be built only from what all saw.

    The remark is in the log. It never reaches the summarizer's context, so it cannot
    reach the prose, so it cannot reach the speaker's brief.
    """
    events = list(table.store.read())
    remarks = {
        e.envelope.event_id for e in events if e.envelope.type == SideRemarkOverheard.__name__
    }
    assert remarks, "the story lets a remark slip, so the check is not vacuous"

    context = summarising(events)

    read = {s.event_id for s in context.read if isinstance(s, EventRef)}
    assert read, "the summarizer read the room's events"
    assert not read & remarks


def test_summaries_reach_a_party_labelled_as_prose(table: Table):
    context = proposing(table.view("eu").settled(), "uk", next(iter(table.clauses)))

    recap = [p for p in context.parts if p.slot == "recap"]
    assert recap, "the story ran with a chronicle, so there are summaries to label"
    assert all(p.register is Register.generated for p in recap)
    assert all(isinstance(s, SectionRef) for p in recap for s in p.sources)
    assert "advisory prose, not canon" in render_proposing(context)


def test_the_same_position_shows_a_party_the_same_thing(table: Table):
    clause = next(iter(table.clauses))
    first = proposing(table.view("uk").settled(), "eu", clause)
    second = proposing(table.view("uk").settled(), "eu", clause)

    assert first.describe() == second.describe()
    assert render_proposing(first) == render_proposing(second)


def test_a_settled_view_refuses_to_read_after_the_log_moves(tmp_path: Path):
    """A context half from before an event and half from after it describes no moment."""
    table = _replayed(tmp_path / "moving.db")
    try:
        clause = next(iter(table.clauses))
        view = table.view("eu").settled()
        table.believe(
            held("eu"),
            "position",
            (table.seats["eu"], table.clauses[clause]),
            "a late change of mind",
            table.tabled_at[clause],
        )

        with pytest.raises(StaleRead):
            view.own()
    finally:
        table.close()


def test_a_settled_view_reads_on_when_the_log_moves_without_a_belief(tmp_path: Path):
    """Tabling a clause changes no belief, so the view still describes its moment."""
    table = _replayed(tmp_path / "moving.db")
    try:
        view = table.view("eu").settled()
        before = view.own()
        table.table_clause("a late addition")

        assert view.own() == before
    finally:
        table.close()
