"""
Tier 1: the pin ledger at the provider boundary, driven by a pydantic-ai fake.

The effect here is a FunctionModel-backed model call. The fake is scripted to return a
DIFFERENT answer on every invocation — so if reuse ever loses to re-drawing, the
assertions below cannot pass. conftest sets ALLOW_MODEL_REQUESTS = False; nothing in
this file can reach a real provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from anagrapheus.timeline.pins import PinLedger, pin_key, register_builtin_events
from anagrapheus.timeline.registry import EventTypeRegistry
from anagrapheus.timeline.sqlite import SQLiteEventStore

if TYPE_CHECKING:
    from pathlib import Path


class ScriptedCaller:
    """A model caller whose responses differ on every call, by construction."""

    def __init__(self) -> None:
        self.invocations = 0
        self._agent = Agent(FunctionModel(self._respond))

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(f"answer #{self.invocations}")])

    def __call__(self, prompt: str) -> dict[str, str]:
        self.invocations += 1
        result = self._agent.run_sync(prompt)
        return {"text": result.output}


@pytest.fixture
def ledger(tmp_path: Path) -> PinLedger:
    types = EventTypeRegistry()
    register_builtin_events(types)
    return PinLedger(SQLiteEventStore(tmp_path / "t.db"), types)


def test_same_call_retried_consumes_entropy_once(ledger: PinLedger):
    caller = ScriptedCaller()
    key = pin_key("model_call", {"task": "summarize", "subject": "doc-7"})

    outcomes = [
        ledger.resolve(
            key,
            kind="model_call",
            effect=lambda: caller("summarize doc-7"),
            interpret=lambda raw: raw["text"],
        )
        for _ in range(4)
    ]

    assert caller.invocations == 1
    assert outcomes == ["answer #1"] * 4  # answers #2..#4 were never drawn


def test_materially_different_call_draws_fresh(ledger: PinLedger):
    caller = ScriptedCaller()

    first = ledger.resolve(
        pin_key("model_call", {"task": "summarize", "subject": "doc-7"}),
        kind="model_call",
        effect=lambda: caller("summarize doc-7"),
        interpret=lambda raw: raw["text"],
    )
    second = ledger.resolve(
        pin_key("model_call", {"task": "summarize", "subject": "doc-8"}),
        kind="model_call",
        effect=lambda: caller("summarize doc-8"),
        interpret=lambda raw: raw["text"],
    )

    assert caller.invocations == 2
    assert (first, second) == ("answer #1", "answer #2")


def test_reinterpretation_never_refetches(ledger: PinLedger):
    """Changing how the answer is read reuses the pinned raw — the anti-fishing rule."""
    caller = ScriptedCaller()
    key = pin_key("model_call", {"task": "classify", "subject": "doc-7"})

    def call() -> dict[str, str]:
        return caller("classify doc-7")

    as_text = ledger.resolve(key, kind="model_call", effect=call, interpret=lambda r: r["text"])
    as_len = ledger.resolve(key, kind="model_call", effect=call, interpret=lambda r: len(r["text"]))

    assert caller.invocations == 1
    assert as_len == len(as_text)
