"""
Milestone-5 acceptance: a streamed agent turn, pinned and replayed.

The story is NVM's: several calls happen behind the scenes, then one final call — the
Narrator — streams its answer to the player to hide latency. Every draw is pinned, the
turn is committed, and the whole thing replays with the model **rigged to fail if it is
reached at all**.

The milestone is the last part. M4 refused to stream, and the refusal was wider than it
looked: ``run_stream``, ``run_stream_events`` and an ``event_stream_handler`` on an
ordinary ``run()`` all route through ``request_stream``, so a pinned agent could not
report progress by any means. All three work here, against the same pinned model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel

from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
    register_builtin_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator
    from pathlib import Path

    from pydantic_ai import RunContext
    from pydantic_ai.messages import AgentStreamEvent, ModelMessage
    from pydantic_ai.models.function import AgentInfo


def test_milestone_5_acceptance(tmp_path: Path):
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(tmp_path / "narrator.db")
    ledger = PinLedger(store, types)

    opens: list[int] = []
    # Flipped after the live turn. The model object must stay the same -- its identity
    # is in the key, so substituting one would make the replay miss for that reason.
    reachable = True

    async def narrate(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        assert reachable, "the replayed turn reached the model"
        opens.append(len(opens))
        for chunk in ["The harbour ", "is quiet ", "tonight."]:
            yield chunk

    prose = "The harbour is quiet tonight."

    # -- the live turn: streamed to the player, pinned on the way past ----------------
    live = Agent(model=PinnedModel(FunctionModel(stream_function=narrate), ledger))

    async def tell(agent: Agent[None, str]) -> tuple[str, int]:
        chunks: list[str] = []
        async with agent.run_stream("what do I see?") as result:
            async for text in result.stream_text(delta=True):
                chunks.append(text)
            return await result.get_output(), len(chunks)

    import asyncio

    said, delivered = asyncio.run(tell(live))
    assert said == prose
    assert delivered > 0, "the player saw it arrive in pieces"
    assert len(opens) == 1

    ledger.commit()
    events = list(store.read())
    kinds = [str(e.payload["kind"]) for e in events if e.envelope.type == "PinPromoted"]
    assert kinds == ["model_call"]
    store.close()

    # -- replay: three surfaces, one pin, and the model never reached -----------------
    reachable = False
    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed = Agent(model=PinnedModel(FunctionModel(stream_function=narrate), replay))

    again, _ = asyncio.run(tell(replayed))
    assert again == prose

    # run_stream_events -- refused outright before this milestone
    async def as_events() -> int:
        seen = 0
        async with replayed.run_stream_events("what do I see?") as stream:
            async for _ in stream:
                seen += 1
        return seen

    assert asyncio.run(as_events()) > 0

    # an event_stream_handler on a plain run() -- likewise refused before
    handled: list[AgentStreamEvent] = []

    async def handler(
        ctx: RunContext[object], stream: AsyncIterable[AgentStreamEvent]
    ) -> None:
        async for event in stream:
            handled.append(event)

    result = asyncio.run(replayed.run("what do I see?", event_stream_handler=handler))
    assert result.output == prose
    assert handled, "the handler saw the replayed stream"

    assert len(opens) == 1, "the model was opened once, for the live turn only"
    assert replay.divergence is None
