"""
Tier 3: does this still work against an actual provider?

Note 00 §3 planned four tiers and this is the one that never got built — six milestones
running, while tiers 0–2 grew to nearly three hundred tests. §8 asked whether it gates
a release and answered its own objection: *"a nightly that nobody reads is not a smoke
test."* **Decided: it gates a release.** It runs on demand and on a release tag, never
on a pull request — §5 records that fork PRs cannot see repository secrets, so a PR
trigger would fail every outside contribution.

**What it asserts is wiring, not quality.** §6 keeps tests and evals apart: an eval
measures the model's behaviour under change, a test measures ours holding the model
fixed. Nothing here inspects what the model *said*.

**Why it cannot be replaced by tiers 1–2.** Every recorded pin in this repository was
produced by a `FunctionModel`, so every canonicalization test has only ever seen
synthetic response shapes. This is the one place a pin is recorded from a real provider
— and therefore the only place a policy bug that real shapes trigger can surface.

Cost: roughly 500 input and 200 output tokens, about $0.0075 a run.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PIN_KIND, PinnedModel
from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.tier3,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        # Skipped rather than failed: a contributor running the suite locally must not
        # meet a red test they have no way to fix.
        reason="tier 3 needs ANTHROPIC_API_KEY",
    ),
]

#: The library's documented default. Not downgraded for cost -- at this size the whole
#: run is under a cent, so a cheaper model would buy nothing and test something the
#: consumer is less likely to be using.
MODEL = "claude-opus-5"


def test_a_real_call_is_pinned_and_replays_offline(tmp_path: Path):
    """
    One live call through `PinnedModel`, then the same run from the log with no provider.

    Deliberately **through** the wrapper: an unwrapped call would test pydantic-ai
    rather than this library, and it is the wrapper's key-building and round-tripping
    that tier 3 exists to check against real response shapes.
    """
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(tmp_path / "tier3.db")
    ledger = PinLedger(store, types)

    agent = Agent(model=PinnedModel(AnthropicModel(MODEL), ledger))
    live = agent.run_sync("Reply with exactly the word: acknowledged.")

    # The only claim made about the content: something came back and validated. What it
    # says is the model's business -- §6's tests-vs-evals line.
    assert live.output, "a real response validated as a ModelResponse"

    ledger.commit()
    promoted = [e for e in store.read() if e.envelope.type == "PinPromoted"]
    assert len(promoted) == 1
    assert promoted[0].payload["kind"] == PIN_KIND

    # -- replay: same key, same answer, provider never touched -------------------------
    events = list(store.read())
    store.close()
    replay = ReplayPins.from_events(events, types, on_miss="strict")
    replayed = Agent(model=PinnedModel(AnthropicModel(MODEL), replay))

    again = replayed.run_sync("Reply with exactly the word: acknowledged.")

    assert again.output == live.output
    assert replay.divergence is None, (
        "a real response shape must key identically on the way back in -- "
        "this is the assertion no synthetic model can make"
    )
