"""
Streaming, pinned: the stream is opened once and replayed from what it accumulated.

Every test counts how many times the stream was *opened*, which is a sharper assertion
than counting responses: a reused pin must cost no connection at all, not one opened
and discarded.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters

from anagrapheus.provider import PIN_KIND, PinnedModel, PinnedStreamedResponse
from anagrapheus.timeline import ReplayPins
from anagrapheus.timeline.replay import PinMiss

from .conftest import Counted, CountedStream, Rig, says

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponseState


async def _streamed(agent: Agent[None, str], prompt: str) -> str:
    async with agent.run_stream(prompt) as result:
        return await result.get_output()


def test_a_streamed_call_opens_the_stream_once(rig: Rig):
    """The whole point, stated as a count of connections rather than of answers."""
    counted = CountedStream(["Lisbon ", "is the ", "capital."])
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    first = asyncio.run(_streamed(agent, "where?"))
    second = asyncio.run(_streamed(agent, "where?"))

    assert first == second == "Lisbon is the capital."
    assert counted.opens == 1


def test_strict_replay_never_opens_the_stream(rig: Rig):
    """
    M4's property, carried through a path that would otherwise connect first.

    The miss raises before ``effect`` is entered, so "no model was reached" stays
    something the test holds rather than hopes — which matters more here than for a
    plain request, because opening a stream is itself the side effect.
    """
    counted = CountedStream(["Lisbon"])
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    asyncio.run(_streamed(live, "where?"))
    rig.ledger.commit()
    opened = counted.opens

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    assert asyncio.run(_streamed(replayed, "where?")) == "Lisbon"
    assert counted.opens == opened == 1
    assert replay.divergence is None


def test_a_changed_prompt_diverges_rather_than_pretending(rig: Rig):
    """A streamed replay announces a miss exactly as the plain path does."""
    counted = CountedStream(["Lisbon"])
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    asyncio.run(_streamed(live, "where?"))
    rig.ledger.commit()

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    with pytest.raises(PinMiss):
        asyncio.run(_streamed(replayed, "somewhere else?"))
    assert replay.divergence is not None
    assert replay.divergence.kind == PIN_KIND


def test_a_streamed_pin_replays_through_the_plain_path(rig: Rig):
    """
    Cross-transport, the first direction: streaming is *how you receive*, not what you
    ask.

    §4a keys on the semantic identity of the call, so the transport is not in the key.
    This is that decision made falsifiable — if the key ever gained a streaming flag,
    this is the test that would fail.
    """
    counted = CountedStream(["Lisbon ", "is the ", "capital."])
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    asyncio.run(_streamed(live, "where?"))
    rig.ledger.commit()

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    assert replayed.run_sync("where?").output == "Lisbon is the capital."
    assert counted.opens == 1


def test_a_plain_pin_replays_as_a_stream(rig: Rig):
    """
    Cross-transport, the other direction, and the one a corpus depends on.

    A pin recorded by a non-streaming run must serve a streamed replay. Without this,
    "same key" would be true in name while the two paths kept separate corpora.
    """
    counted = Counted(says("Lisbon is the capital."))
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    live.run_sync("where?")
    rig.ledger.commit()
    drawn = counted.count

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = Agent(model=PinnedModel(counted.model(), replay))

    assert asyncio.run(_streamed(replayed, "where?")) == "Lisbon is the capital."
    assert counted.count == drawn == 1


def test_an_abandoned_stream_still_pins_what_arrived(rig: Rig):
    """
    A consumer that breaks out early leaves a partial, and the partial is the pin.

    Recording nothing would make the next attempt re-draw — the effect happening twice,
    which is the one thing the layer promises against. Recording it as *complete* would
    be worse: replay would hand back a truncated answer dressed as a finished one.
    """
    counted = CountedStream(["Lisbon ", "is the ", "capital."])
    model = PinnedModel(counted.model(), rig.ledger)

    async def bail_out() -> None:
        async with model.request_stream(
            [_prompt("where?")], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                break  # one event, then abandon

    asyncio.run(bail_out())
    rig.ledger.commit()

    promoted = [e for e in rig.store.read() if e.envelope.type == "PinPromoted"]
    assert len(promoted) == 1, "an abandoned stream must still leave a pin"
    result = promoted[0].payload["result"]
    assert isinstance(result, dict)
    assert result["state"] != "complete", "a partial must not be pinned as finished"


def test_a_stream_that_fails_mid_way_still_pins_what_arrived(rig: Rig):
    """
    The exception path, which is the one the ``finally`` exists for.

    Breaking out of a stream leaves the block *normally*, so it records either way —
    this is the case that does not. If the write were merely the last statement rather
    than a ``finally``, a consumer that raised mid-stream would lose the draw entirely
    and the next attempt would re-open the connection, performing the effect twice.
    """
    counted = CountedStream(["Lisbon ", "is the ", "capital."])
    model = PinnedModel(counted.model(), rig.ledger)

    class Boom(Exception):
        pass

    async def blow_up() -> None:
        async with model.request_stream(
            [_prompt("where?")], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                raise Boom

    with pytest.raises(Boom):
        asyncio.run(blow_up())
    rig.ledger.commit()

    promoted = [e for e in rig.store.read() if e.envelope.type == "PinPromoted"]
    assert len(promoted) == 1, "a stream that raised must still leave its pin"


@pytest.mark.parametrize("state", ["complete", "incomplete", "suspended", "interrupted"])
def test_the_replayed_stream_reproduces_the_state_it_ended_in(state: ModelResponseState):
    """
    All four states survive the round trip, including the three that mean "not finished".

    ``get()`` derives its answer from three separate flags, so reproducing a state is
    not a matter of copying one field — and getting it wrong would silently turn every
    interrupted stream into a complete one.
    """
    pinned = ModelResponse(parts=[TextPart("Lisbon")], model_name="fake:m", state=state)

    stream = PinnedStreamedResponse(
        model_request_parameters=ModelRequestParameters(), pinned=pinned
    )

    async def drain() -> ModelResponse:
        async for _ in stream:
            pass
        return stream.get()

    rebuilt = asyncio.run(drain())
    assert rebuilt.state == state
    assert rebuilt.parts == pinned.parts


def test_the_replayed_events_reassemble_into_the_pin(rig: Rig):
    """
    The synthetic stream is not lossy: what the consumer sees adds back up to the pin.

    Chunk boundaries are deliberately *not* reproduced — they were the provider's and
    are not in the pin — so this asserts the content, which is the part that must hold.
    """
    counted = CountedStream(["Lisbon ", "is the ", "capital."])
    live = Agent(model=PinnedModel(counted.model(), rig.ledger))
    asyncio.run(_streamed(live, "where?"))
    rig.ledger.commit()

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    model = PinnedModel(counted.model(), replay)

    async def collect() -> ModelResponse:
        async with model.request_stream(
            [_prompt("where?")], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                pass
            return stream.get()

    rebuilt = asyncio.run(collect())
    assert "".join(p.content for p in rebuilt.parts if isinstance(p, TextPart)) == (
        "Lisbon is the capital."
    )


def test_concurrent_streams_on_one_key_open_one_stream(rig: Rig):
    """
    Two callers, one key, one connection.

    The lock has to span the whole stream rather than just its opening: the pin does not
    exist until the first caller has finished consuming, so a lock released at the draw
    would let the second caller miss and open a second stream.
    """
    counted = CountedStream(["Lisbon"])
    model = PinnedModel(counted.model(), rig.ledger)

    async def once() -> str:
        async with model.request_stream(
            [_prompt("where?")], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                pass
            response = stream.get()
        return "".join(p.content for p in response.parts if isinstance(p, TextPart))

    async def both() -> list[str]:
        return list(await asyncio.gather(once(), once()))

    assert asyncio.run(both()) == ["Lisbon", "Lisbon"]
    assert counted.opens == 1


def test_unrelated_streams_are_not_serialized(rig: Rig):
    """
    The lock is per key, so two different calls still overlap.

    Worth asserting because the fix for the previous test is a lock held for the whole
    stream — the widest hold in the library — and a global one would quietly turn
    concurrent tool-driven streams into a queue.
    """
    counted = CountedStream(["Lisbon"])
    model = PinnedModel(counted.model(), rig.ledger)
    inside = 0
    peak = 0

    async def once(prompt: str) -> None:
        nonlocal inside, peak
        async with model.request_stream(
            [_prompt(prompt)], None, ModelRequestParameters()
        ) as stream:
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0)
            async for _ in stream:
                pass
            inside -= 1

    async def several() -> None:
        await asyncio.gather(*(once(f"q{n}") for n in range(4)))

    asyncio.run(several())
    assert peak == 4, "different keys must not queue behind one another"
    assert counted.opens == 4


def _prompt(text: str):  # noqa: ANN202 -- a one-line fixture, not the subject
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return ModelRequest(parts=[UserPromptPart(content=text)])


def test_a_stream_closed_by_another_task_still_unwinds(rig: Rig):
    """
    The stream may be finalized by a different task than the one that opened it.

    ``stream_text(delta=True)`` groups deltas temporally, which tears the stream down
    from another task. That is why the gate in ``resolve_stream`` is an ``Event`` and
    not an ``anyio.Lock``: a lock is task-affine and refuses a release by a task that
    did not acquire it, so holding one across the ``yield`` raises ``RuntimeError`` on
    the way out — after the answer has been delivered, which is the worst moment.
    """
    counted = CountedStream(["The harbour ", "is quiet ", "tonight."])
    agent = Agent(model=PinnedModel(counted.model(), rig.ledger))

    async def by_delta() -> list[str]:
        chunks: list[str] = []
        async with agent.run_stream("what do I see?") as result:
            async for delta in result.stream_text(delta=True):
                chunks.append(delta)
        return chunks

    chunks = asyncio.run(by_delta())

    assert "".join(chunks) == "The harbour is quiet tonight."
    assert counted.opens == 1
