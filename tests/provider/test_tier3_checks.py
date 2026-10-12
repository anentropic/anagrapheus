"""
Tier 3's checks, run offline in the default suite so they cannot rot between releases.

Tier 3 itself runs only at release, against real providers. A check broken by an API
change — pydantic-ai's, or this library's — would otherwise be found on release day, by
the release. Running the same bodies against ``TestModel`` here proves their plumbing on
every commit; only the real response shapes are left for tier 3 to supply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.settings import ModelSettings

from tests.tier3.checks import CHECKS, check_tool

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo


@pytest.mark.parametrize("check", CHECKS.values(), ids=CHECKS.keys())
def test_the_check_runs_end_to_end_offline(check: Callable[[Model, Path], None], tmp_path: Path):
    check(TestModel(), tmp_path)


def test_the_tool_check_cannot_pass_without_calling_the_tool(tmp_path: Path):
    """
    A model that never calls a tool unless forced to, and the check still exercises one.

    ``TestModel`` calls every tool it is offered, so it cannot tell whether the check's
    step-one forcing works. This model can. Without the forcing a real model that
    answered from memory would let the tool check pass having made one request — the
    multi-step round trip, the one tier 3 most needs, untested and reported green.
    """

    def lazy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings = info.model_settings or ModelSettings()
        forced = "tool_choice" in settings and settings["tool_choice"] == ["lookup"]
        if forced:
            return ModelResponse(parts=[ToolCallPart("lookup", {"clause": "fisheries"})])
        return ModelResponse(parts=[TextPart("from memory: sixty-forty")])

    check_tool(FunctionModel(lazy, model_name="lazy"), tmp_path)
