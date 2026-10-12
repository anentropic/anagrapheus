"""
The demo's command line: model specs in, a clear refusal out when one cannot run.

Tested against the real ``infer_model`` rather than an injected resolver, because the
claim being made is about *its* failures reaching the user cleanly — a missing key
reported before anything runs, not a traceback from inside round one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import examples.negotiation.__main__ as cli
import pytest
from examples.negotiation.__main__ import main
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo
    from pytest import CaptureFixture, MonkeyPatch


def test_with_no_model_it_runs_offline_and_says_the_replay_reached_none(
    capsys: CaptureFixture[str],
):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.lstrip().startswith("lineup"), "it says which model filled each slot first"
    assert "none from a model" in out


def test_a_misspelt_role_is_refused_before_anything_runs(capsys: CaptureFixture[str]):
    with pytest.raises(SystemExit) as exited:
        main(["--model", "updaet=x"])
    assert exited.value.code == 2
    assert "unknown role 'updaet'" in capsys.readouterr().err


def test_a_missing_key_is_reported_before_the_first_draw(
    capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
):
    """
    ``infer_model`` builds the provider at parse time, and the provider wants its key.

    So the refusal arrives as a usage error naming the variable, from the same place
    as every other bad spec — not partway through round one with a negotiation
    half-written to the log.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exited:
        main(["--model", "anthropic:claude-opus-5"])
    assert exited.value.code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_a_model_that_stays_wrong_is_reported_not_raised(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
):
    """A model that misnames the clause even after the retry gets a message, not a trace."""

    def stubborn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        args = {"clause": "a sentence about fish", "concede": False, "because": "no"}
        return ModelResponse(parts=[ToolCallPart("final_result", args)])

    monkeypatch.setattr(cli, "scripted", lambda: FunctionModel(stubborn))
    assert main([]) == 1
    assert "could not be recorded" in capsys.readouterr().err


def test_a_party_not_at_the_table_is_a_usage_error(capsys: CaptureFixture[str]):
    """``ue.propose`` is refused at parse time, with no table opened and no traceback."""
    with pytest.raises(SystemExit) as exited:
        main(["--model", "test", "--model", "ue.propose=test"])
    assert exited.value.code == 2
    assert "ue, not at the table" in capsys.readouterr().err
