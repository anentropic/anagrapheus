"""What a pinned call says about itself in the log: drawn or reused, and what was asked."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent

from anagrapheus.provider import PinnedModel, pinned_tool
from anagrapheus.timeline import ReplayPins
from anagrapheus.timeline.replay import PinMiss

from .conftest import Counted, CountedStream, Rig, says

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models.function import AgentInfo
    from pytest import LogCaptureFixture

MODEL_LOGGER = "anagrapheus.provider.model"


def _calls(caplog: LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == MODEL_LOGGER]


def test_a_call_says_whether_it_reached_the_model(rig: Rig, caplog: LogCaptureFixture):
    """
    The one fact a log line about a pinned call must get right.

    The resolver decides whether to draw and does not report which it chose, so the
    model wrapper learns it from its own effect being run. A line that said "drawn"
    for a reused pin would make a replay look like it was spending money.
    """
    counted = Counted(says("Lisbon"))
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    with caplog.at_level(logging.INFO, logger="anagrapheus"):
        agent.run_sync("where?")
        agent.run_sync("where?")

    first, second = (r.getMessage() for r in _calls(caplog))
    assert counted.count == 1
    assert " drawn " in first
    assert "reused from pin" in second


def test_at_debug_a_call_carries_its_conversation(rig: Rig, caplog: LogCaptureFixture):
    """The UI reads ``detail`` to show what was asked and answered, so it is structured."""
    agent = Agent(model=PinnedModel(Counted(says("Lisbon")).model(), rig.ledger))

    with caplog.at_level(logging.DEBUG, logger="anagrapheus"):
        agent.run_sync("where?")

    (record,) = _calls(caplog)
    detail = record.__dict__["detail"]
    assert detail["drawn"] is True
    assert detail["request"][-1]["parts"][-1] == {"part_kind": "user-prompt", "content": "where?"}
    assert detail["response"]["parts"] == [{"part_kind": "text", "content": "Lisbon"}]


def test_above_debug_the_conversation_is_not_serialized(rig: Rig, caplog: LogCaptureFixture):
    """Serializing every conversation is a cost, paid only when someone will read it."""
    agent = Agent(model=PinnedModel(Counted(says("Lisbon")).model(), rig.ledger))

    with caplog.at_level(logging.INFO, logger="anagrapheus"):
        agent.run_sync("where?")

    (record,) = _calls(caplog)
    assert "detail" not in record.__dict__


def test_a_failed_call_is_a_warning(rig: Rig, caplog: LogCaptureFixture):
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("provider fell over")

    agent = Agent(model=PinnedModel(Counted(broken).model(), rig.ledger))

    with caplog.at_level(logging.INFO, logger="anagrapheus"), pytest.raises(RuntimeError):
        agent.run_sync("where?")

    (record,) = _calls(caplog)
    assert record.levelno == logging.WARNING
    assert "provider fell over" in record.getMessage()


def test_the_library_is_silent_until_configured():
    """A library logs and never configures: without a handler, nothing reaches stderr."""
    handlers = logging.getLogger("anagrapheus").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)


async def _streamed(agent: Agent[None, str], prompt: str) -> str:
    async with agent.run_stream(prompt) as result:
        return await result.get_output()


def test_a_streamed_call_is_logged_once_it_has_closed(rig: Rig, caplog: LogCaptureFixture):
    agent = Agent(model=PinnedModel(CountedStream(["Lis", "bon"]).model(), rig.ledger))

    with caplog.at_level(logging.INFO, logger="anagrapheus"):
        asyncio.run(_streamed(agent, "where?"))

    (record,) = _calls(caplog)
    assert record.levelno == logging.INFO
    assert " drawn " in record.getMessage()


def test_a_stream_that_fails_to_open_is_a_warning(rig: Rig, caplog: LogCaptureFixture):
    """
    Review finding: only the caller's use of an open stream was covered.

    A strict replay that misses raises while the stream is being *opened*, before there
    is anything to use -- and that call left no record at all.
    """
    replay = ReplayPins.from_events([], rig.types, on_miss="strict")
    agent = Agent(model=PinnedModel(CountedStream(["Lisbon"]).model(), replay))

    with caplog.at_level(logging.INFO, logger="anagrapheus"), pytest.raises(PinMiss):
        asyncio.run(_streamed(agent, "where?"))

    (record,) = _calls(caplog)
    assert record.levelno == logging.WARNING
    assert "failed" in record.getMessage()


def test_a_failed_tool_is_a_warning(rig: Rig, caplog: LogCaptureFixture):
    """Review finding: a tool that raised left no record, though a failed call should."""

    def fetch(url: str) -> str:
        raise ConnectionError("archive unreachable")

    with caplog.at_level(logging.INFO, logger="anagrapheus"), pytest.raises(ConnectionError):
        asyncio.run(pinned_tool(rig.ledger, fetch)("http://example.test"))

    (record,) = [r for r in caplog.records if r.name == "anagrapheus.provider.tools"]
    assert record.levelno == logging.WARNING
    assert "archive unreachable" in record.getMessage()
