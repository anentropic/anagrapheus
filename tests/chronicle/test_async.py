"""The awaited path: an async summarizer, drawn from inside a running event loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.chronicle import ChronicleFold, Consolidation, EventTypes
from anagrapheus.chronicle.fold import FOLD_NAME
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import PinLedger, Projector, ReplayPins

from .conftest import NAMESPACE, build_rig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    from anagrapheus.timeline import PinResolver
    from anagrapheus.timeline.envelope import StoredEvent

    from .conftest import Rig


class AwaitedSummary:
    """An async summarizer drawing through a pinned model with ``Agent.run``."""

    def __init__(self, resolver: PinResolver, drawn: list[int]) -> None:
        self._resolver = resolver
        self._drawn = drawn

    def _write(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # A different answer every time, so a re-draw is visible as one.
        self._drawn.append(len(self._drawn))
        return ModelResponse(parts=[TextPart(f"account #{len(self._drawn)}")])

    async def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        what = ", ".join(str(e.payload.get("what", e.envelope.type)) for e in events)
        keys: list[str] = []
        model = PinnedModel(FunctionModel(self._write), self._resolver, on_key=keys.append)
        result = await Agent(model).run(f"summarise: {what}")
        return Consolidation(prose=result.output, pins=tuple(keys))


def install(rig: Rig, summarize: AwaitedSummary) -> Projector:
    fold = ChronicleFold(
        rig.chronicle,
        rig.fold._reader,  # noqa: SLF001 -- the rig's reader, shared
        boundary=EventTypes((NAMESPACE, "UnitClosed")),
        summarize=summarize,
    )
    return Projector(rig.store, rig.fold._reader, [fold])  # noqa: SLF001


def test_an_async_summarizer_consolidates_inside_a_running_loop(rig: Rig):
    """The pins resolve through the ledger's awaited path, on the loop's own thread."""
    ledger = PinLedger(rig.store, rig.types)
    drawn: list[int] = []
    projector = install(rig, AwaitedSummary(ledger, drawn))
    rig.append(rig.step("left the harbour"), rig.close("one"))
    rig.append(rig.step("made landfall"), rig.close("two"))

    async def main() -> dict[str, int]:
        return await projector.catch_up_async()

    assert asyncio.run(main()) == {FOLD_NAME: 4}
    sections = list(rig.chronicle.sections())
    assert [(s.anchor.from_seq, s.anchor.to_seq) for s in sections] == [(1, 2), (3, 4)]
    assert [s.prose for s in sections] == ["account #1", "account #2"]
    assert all(len(s.pins) == 1 for s in sections)
    assert len(drawn) == 2
    ledger.commit()
    promoted = [e for e in rig.store.read() if e.envelope.type == "PinPromoted"]
    assert len(promoted) == 2


def test_an_async_consolidation_replays_bit_exact(rig: Rig):
    """Committed pins replay through the awaited rebuild without reaching the model."""
    ledger = PinLedger(rig.store, rig.types)
    drawn: list[int] = []
    rig.append(rig.step("left the harbour"), rig.close("one"))
    asyncio.run(install(rig, AwaitedSummary(ledger, drawn)).catch_up_async())
    before = [(s.anchor, s.prose, s.pins) for s in rig.chronicle.sections()]
    ledger.commit()

    replay = ReplayPins.from_events(rig.store.read(), rig.types)
    rebuilt = install(rig, AwaitedSummary(replay, drawn)).rebuild_async(
        FOLD_NAME,
        reset=rig.chronicle.reset,
        reset_in_transaction=True,
        discard_generated=True,
    )
    asyncio.run(rebuilt)

    assert [(s.anchor, s.prose, s.pins) for s in rig.chronicle.sections()] == before
    assert len(drawn) == 1, "the rebuild reached no model"
    assert replay.divergence is None


def test_a_sync_summarizer_works_under_catch_up_async(rig: Rig):
    rig.append(rig.step("a"), rig.close("one"))
    asyncio.run(rig.projector.catch_up_async())
    assert rig.summarizer.calls == [(1, 2)]
    assert [a.section_id for a in rig.anchors.all()] == ["1-2"]


def test_catch_up_refuses_an_async_summarizer_without_drawing(tmp_path: Path):
    """The synchronous path cannot await the draw, so it says which path can."""
    rig = build_rig(tmp_path / "chronicle.db")
    try:
        drawn: list[int] = []
        projector = install(rig, AwaitedSummary(PinLedger(rig.store, rig.types), drawn))
        rig.append(rig.step("a"), rig.close("one"))

        with pytest.raises(TypeError, match="catch_up_async"):
            projector.catch_up()
        assert drawn == []
        assert rig.store.read_position(FOLD_NAME) == 0
        assert list(rig.anchors.all()) == []
    finally:
        rig.store.close()


def test_a_failed_async_draw_leaves_the_chronicle_and_position_unchanged(rig: Rig):
    class Failing:
        async def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
            await asyncio.sleep(0)
            if any(e.payload.get("label") == "two" for e in events):
                raise RuntimeError("the model is down")
            return Consolidation(prose="fine")

    fold = ChronicleFold(
        rig.chronicle,
        rig.fold._reader,  # noqa: SLF001
        boundary=EventTypes((NAMESPACE, "UnitClosed")),
        summarize=Failing(),
    )
    projector = Projector(rig.store, rig.fold._reader, [fold])  # noqa: SLF001
    rig.append(rig.step("a"), rig.close("one"), rig.step("b"), rig.close("two"))

    with pytest.raises(RuntimeError, match="the model is down"):
        asyncio.run(projector.catch_up_async())
    assert list(rig.anchors.all()) == []
    assert rig.store.read_position(FOLD_NAME) == 0
