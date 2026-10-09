"""
Choosing a model per role, and comparing one role's model with the rest held fixed.

The parsing tests are about refusing lineups that would *run* but not the way they
read — a typo that silently falls back to the default is an evaluation of the wrong
model. The swap tests are the reason the lineup exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from examples.negotiation.lineup import Lineup, LineupError
from examples.negotiation.scripted import respond, scripted
from examples.negotiation.story import negotiate
from pydantic_ai.messages import ModelResponse, ToolCallPart

from anagrapheus.timeline import ReplayPins

if TYPE_CHECKING:
    from pathlib import Path

    from examples.negotiation.story import Negotiation
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo

    from anagrapheus.timeline import StoredEvent


def _named(spec: str) -> Model:
    """A resolver that needs no API key: every spec becomes a scripted model of that name."""
    return scripted(name=spec)


# -- parsing ------------------------------------------------------------------------------


def test_the_most_specific_slot_wins():
    lineup = Lineup.parse(
        ["base", "update=reader", "eu.propose=eu-proposer"],
        resolve=_named,
    )

    assert lineup.model_for("propose", "eu").model_id == "function:eu-proposer"
    assert lineup.model_for("propose", "uk").model_id == "function:base"
    assert lineup.model_for("update", "eu").model_id == "function:reader"
    assert lineup.model_for("summarize").model_id == "function:base"


@pytest.mark.parametrize(
    ("specs", "complaint"),
    [
        (["base", "updaet=x"], "unknown role"),
        (["base", "update=x", "update=y"], "named twice"),
        (["base", "eu.summarize=x"], "one chronicle"),
        (["propose=x"], "nothing fills update, summarize"),
        (["base", "update="], "names no model"),
    ],
)
def test_a_lineup_that_reads_differently_from_how_it_runs_is_refused(
    specs: list[str], complaint: str
):
    """
    Each of these would otherwise run — just not as written.

    A misspelt role, a slot named twice with the last silently winning, a per-party
    summarizer when there is one shared chronicle, a role left to nothing. In an
    evaluation every one of them reports a result for a model that did not take part.
    """
    with pytest.raises(LineupError, match=complaint):
        Lineup.parse(specs, resolve=_named)


def test_a_party_that_is_not_seated_is_refused_before_any_draw(tmp_path: Path):
    """``ue.propose`` is a typo that runs: the EU gets the default, silently."""
    drawn: list[str] = []
    lineup = Lineup(
        default=scripted(drawn.append),
        parties={("ue", "propose"): scripted(name="typo")},
    )

    with pytest.raises(LineupError, match="ue, not at the table"):
        negotiate(tmp_path / "typo.db", models=lineup)
    assert drawn == [], "refused before the first model call, not partway through"


# -- the controlled swap ------------------------------------------------------------------


def _baseline(tmp_path: Path) -> tuple[Negotiation, list[StoredEvent]]:
    """Record the four-round story with every role on the scripted model."""
    run = negotiate(tmp_path / "baseline.db", models=scripted())
    run.commit()
    events = list(run.table.store.read())
    run.table.close()
    return run, events


def _offers(run: Negotiation) -> list[tuple[int, str, bool]]:
    return [(r.number, t.party, t.offer.concede) for r in run.rounds for t in r.turns]


def test_swapping_one_role_draws_only_that_role(tmp_path: Path):
    """
    Replay a recorded run with the update role on a different model: only it draws.

    The contender behaves identically to the baseline but has its own name, so its
    keys differ and every update call misses. Everything else — every proposal, every
    archive lookup, every consolidation — replays from the baseline, and the default
    model is never reached. That is the property an evaluation of one role needs: the
    only thing that varied is the thing under test.

    The contender must have a name of its own. Two scripted models with one name are one
    model to the ledger, so a "swap" to a second copy would hit the baseline's pins and
    never draw; the first assertion is what catches that.
    """
    baseline, events = _baseline(tmp_path)
    contender: list[str] = []
    reached_default: list[str] = []

    swapped = negotiate(
        tmp_path / "swapped.db",
        models=Lineup(
            default=scripted(reached_default.append),
            roles={"update": scripted(contender.append, name="contender")},
        ),
        resolver=lambda t: ReplayPins.from_events(events, t.types, on_miss="record"),
    )
    try:
        assert len(contender) == 8, "every update call -- two parties, four rounds -- drew"
        assert reached_default == [], "nothing upstream or alongside it re-drew"
        assert isinstance(swapped.pins, ReplayPins)
        divergence = swapped.pins.divergence
        assert divergence is not None, "the replay says where it stopped being one"
        assert divergence.after_hits > 0, "and something replayed before it did"
        assert _offers(swapped) == _offers(baseline), "same behaviour, same negotiation"
    finally:
        swapped.table.close()


def test_a_different_update_model_changes_the_negotiation(tmp_path: Path):
    """
    A contender that reads every opponent as immovable, in the update role only.

    Round one replays from the baseline — it is upstream of the first update call. From
    round two the briefs carry "Our read of their bottom line: never", so those prompts
    differ, those proposals miss and re-draw, and the parties concede a round early.

    **This test could not pass before the reading reached the brief.** The update role's
    output went nowhere, so any model in that slot produced the same negotiation, and an
    evaluation would have scored every model an equally good updater.
    """
    baseline, events = _baseline(tmp_path)
    reached_default: list[str] = []

    def pessimist(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        part = respond(messages, info).parts[0]
        assert isinstance(part, ToolCallPart)
        return ModelResponse(
            parts=[ToolCallPart(part.tool_name, {**part.args_as_dict(), "value": "never"})]
        )

    swapped = negotiate(
        tmp_path / "pessimist.db",
        models=Lineup(
            default=scripted(reached_default.append),
            roles={"update": scripted(name="pessimist", respond_with=pessimist)},
        ),
        resolver=lambda t: ReplayPins.from_events(events, t.types, on_miss="record"),
    )
    try:
        before, after = _offers(baseline), _offers(swapped)
        assert after[:2] == before[:2], "round one is upstream of the swap and replays"
        assert any(concede for number, _, concede in after if number == 2), (
            "read as immovable, a party concedes in round two"
        )
        assert not any(concede for number, _, concede in before if number == 2)
        assert reached_default, "downstream proposals re-drew, because their prompts changed"
    finally:
        swapped.table.close()
