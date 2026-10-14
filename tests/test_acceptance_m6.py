"""
Milestone-6 acceptance: a generative projection, consolidated and replayed.

A log of units is consolidated into chronicle sections by a summarizer that draws
through the **pinned** provider, so the pins' payoff is exercised for real: under
pin hits a generative projection rebuilds bit-exact, and only where a pin misses does
the prose change.

The milestone is the pair of assertions at the end, which no earlier milestone could
make: the anchors come back **identical** while the prose comes back **different** —
and which of the two happens to the prose is decided by whether the pin hit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.chronicle import (
    ChronicleFold,
    Consolidation,
    EventTypes,
    SQLiteChronicleStore,
)
from anagrapheus.chronicle.fold import FOLD_NAME
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import (
    DomainEvent,
    EventKey,
    EventReader,
    EventTypeRegistry,
    GenerativeRebuild,
    PinLedger,
    Projector,
    ReplayPins,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    from anagrapheus.timeline.envelope import StoredEvent

NAMESPACE = "saga"


class StepTaken(DomainEvent, frozen=True):
    what: str


class UnitClosed(DomainEvent, frozen=True):
    label: str


def test_milestone_6_acceptance(tmp_path: Path):
    types = EventTypeRegistry()
    register_builtin_events(types)
    types.register(EventKey(NAMESPACE, "StepTaken", 1), StepTaken)
    types.register(EventKey(NAMESPACE, "UnitClosed", 1), UnitClosed)

    store = SQLiteEventStore(tmp_path / "saga.db")
    reader = EventReader(store, UpcastRegistry(), types)
    chronicle = SQLiteChronicleStore(store.connection)
    ledger = PinLedger(store, types)

    drawn: list[int] = []

    def write(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # A different answer every time, so a rebuild that re-draws is visible as such
        # and a rebuild that reuses is visible as *that*.
        drawn.append(len(drawn))
        return ModelResponse(parts=[TextPart(f"account #{len(drawn)}")])

    keys: list[str] = []
    model = PinnedModel(FunctionModel(write), ledger, on_key=keys.append)

    def summarize(events: Sequence[StoredEvent]) -> Consolidation:
        """The application's, as it must be: the prompt lives here, so the key does."""
        what = ", ".join(str(e.payload.get("what", e.envelope.type)) for e in events)
        keys.clear()
        result = Agent(model=model).run_sync(f"summarise: {what}")
        return Consolidation(prose=result.output, pins=tuple(keys))

    fold = ChronicleFold(
        chronicle,
        reader,
        boundary=EventTypes((NAMESPACE, "UnitClosed")),
        summarize=summarize,
    )
    projector = Projector(store, reader, [fold])

    # -- consolidate two units --------------------------------------------------------
    store.append(
        [types.pending(StepTaken(what="left the harbour")), types.pending(UnitClosed(label="one"))]
    )
    store.append(
        [types.pending(StepTaken(what="made landfall")), types.pending(UnitClosed(label="two"))]
    )
    projector.catch_up()

    anchors_before = list(chronicle.anchors())
    prose_before = [s.prose for s in chronicle.sections()]
    assert [(a.from_seq, a.to_seq) for a in anchors_before] == [(1, 2), (3, 4)]
    assert len(drawn) == 2
    # Provenance recorded, not merely claimed: each section names the pin that made it.
    assert all(s.pins for s in chronicle.sections())
    pins_before = [s.pins for s in chronicle.sections()]

    # -- rebuild is refused by default, because it is neither cheap nor exact ---------
    with pytest.raises(GenerativeRebuild):
        projector.rebuild(FOLD_NAME, reset=chronicle.reset)

    # -- commit: the consolidating draws become ordinary events in the log -----------
    ledger.commit()
    events = list(store.read())
    assert sum(1 for e in events if e.envelope.type == "PinPromoted") == 2

    # -- rebuild through those pins: bit-exact, R61 clause 5's payoff ----------------
    # Not the warm scratch -- that would only show reuse-under-retry. This replays from
    # the log, which is the claim that matters: a consolidation pinned months ago can
    # be reproduced exactly, without reaching a model.
    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed_model = PinnedModel(FunctionModel(write), replay, on_key=keys.append)

    def summarize_replayed(events: Sequence[StoredEvent]) -> Consolidation:
        what = ", ".join(str(e.payload.get("what", e.envelope.type)) for e in events)
        keys.clear()
        output = Agent(model=replayed_model).run_sync(f"summarise: {what}").output
        return Consolidation(prose=output, pins=tuple(keys))

    Projector(
        store,
        reader,
        [
            ChronicleFold(
                chronicle,
                reader,
                boundary=EventTypes((NAMESPACE, "UnitClosed")),
                summarize=summarize_replayed,
            )
        ],
    ).rebuild(FOLD_NAME, reset=chronicle.reset, reset_in_transaction=True, discard_generated=True)

    assert list(chronicle.anchors()) == anchors_before
    assert [s.prose for s in chronicle.sections()] == prose_before, (
        "under pin hits a generative projection rebuilds bit-exact"
    )
    assert len(drawn) == 2, "and reaches no model to do it"
    assert replay.divergence is None
    assert [s.pins for s in chronicle.sections()] == pins_before, (
        "the same keys, which is what makes the bit-exactness checkable rather than lucky"
    )

    # -- rebuild against a cold ledger: same anchors, regenerated prose ---------------
    cold = PinLedger(store, types)
    cold_model = PinnedModel(FunctionModel(write), cold)

    def summarize_cold(events: Sequence[StoredEvent]) -> Consolidation:
        what = ", ".join(str(e.payload.get("what", e.envelope.type)) for e in events)
        keys.clear()
        output = Agent(model=cold_model).run_sync(f"summarise: {what}").output
        return Consolidation(prose=output, pins=tuple(keys))

    cold_fold = ChronicleFold(
        chronicle,
        reader,
        boundary=EventTypes((NAMESPACE, "UnitClosed")),
        summarize=summarize_cold,
    )
    Projector(store, reader, [cold_fold]).rebuild(
        FOLD_NAME, reset=chronicle.reset, reset_in_transaction=True, discard_generated=True
    )

    # The milestone, in two lines.
    assert list(chronicle.anchors()) == anchors_before, "same log, the same anchors"
    assert [s.prose for s in chronicle.sections()] != prose_before, "the content is regenerated"
    assert len(drawn) == 4

    # -- and the carry path, which is why this projection need not be rebuilt at all --
    carried = list(chronicle.export())
    chronicle.reset()
    assert chronicle.load(carried) == len(carried)
    assert list(chronicle.anchors()) == anchors_before

    store.close()
