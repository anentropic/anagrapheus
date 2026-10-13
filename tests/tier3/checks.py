"""
What tier 3 checks against a real provider — written once, run two ways.

Each check takes a model, makes real calls through
:class:`~anagrapheus.provider.PinnedModel`, commits, then replays the same run from the
log with ``on_miss="strict"`` — so the replay *raises* at the first miss, and a check
that finishes reached no model on the way back.

**Why a real provider, and why these four.** A pin stores pydantic-ai's normalized
``ModelResponse``, dumped to JSON and validated back on replay. Every pin in tiers 0–2
came from a ``FunctionModel``, so that round trip has only ever seen synthetic shapes.
The bug class tier 3 exists to catch is a real response that does not survive it: some
field a provider fills that comes back subtly different — a nested ``provider_details``,
tool arguments as a JSON *string* (OpenAI-shaped routes) rather than a dict (Anthropic),
a timestamp's zone. When that happens the next request, built from the replayed
response, keys differently from the live one and the replay diverges.

- :func:`check_plain` — one call, nothing downstream of it.
- :func:`check_structured` — the output tool path, which every structured result in the
  demo takes on every route (``default_structured_output_mode`` is ``tool`` throughout).
- :func:`check_tool` — **the one most likely to find something**, because it is
  multi-step: request two contains request one's response. A response that does not
  round-trip faithfully changes request two's key, and only a multi-step call can show
  that. The tool is forced on step one so the check cannot pass by the model choosing
  not to call it.
- :func:`check_stream` — the accumulated stream is what is pinned (M5), and the replay
  is a synthetic stream rebuilt from it.

**Run two ways.** ``tests/tier3/test_live_smoke.py`` runs them against every route with a
key. ``tests/provider/test_tier3_checks.py`` runs them against ``TestModel`` in the
default suite, because tier 3 runs only at release: a check broken by an API change
would otherwise be found on release day, by the release.

What none of them inspects is what the model *said*. A test measures this
library holding the model fixed; what the model says is an eval.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel, pinned_tool
from anagrapheus.timeline import EventTypeRegistry, PinLedger, ReplayPins, SQLiteEventStore

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai.agent import AgentModelSettings
    from pydantic_ai.models import Model
    from pydantic_ai.settings import ModelSettings
    from pydantic_ai.tools import RunContext

    from anagrapheus.timeline import PinResolver, StoredEvent

__all__ = ["CHECKS", "check_plain", "check_stream", "check_structured", "check_tool"]


class _Rig:
    """A fresh store and ledger for the live half; the events it committed for the replay."""

    def __init__(self, tmp_path: Path) -> None:
        self.types = EventTypeRegistry()
        register_builtin_events(self.types)
        self._store = SQLiteEventStore(tmp_path / "tier3.db")
        self.ledger = PinLedger(self._store, self.types)

    def commit(self) -> list[StoredEvent]:
        self.ledger.commit()
        events = list(self._store.read())
        self._store.close()
        return events

    def replay(self, events: list[StoredEvent]) -> ReplayPins:
        return ReplayPins.from_events(events, self.types, on_miss="strict")


def _pins(events: list[StoredEvent], kind: str) -> int:
    return sum(1 for e in events if e.envelope.type == "PinPromoted" and e.payload["kind"] == kind)


def check_plain(model: Model, tmp_path: Path) -> None:
    """One call, pinned, then answered from the log."""
    rig = _Rig(tmp_path)
    prompt = "Reply with exactly the word: acknowledged."
    live = Agent(model=PinnedModel(model, rig.ledger)).run_sync(prompt).output
    events = rig.commit()
    assert _pins(events, "model_call") == 1

    replay = rig.replay(events)
    again = Agent(model=PinnedModel(model, replay)).run_sync(prompt).output

    assert again == live
    assert replay.divergence is None


class Settlement(BaseModel):
    """What the structured check asks for. Flat on purpose: the shape, not the schema."""

    clause: str
    split: str


def check_structured(model: Model, tmp_path: Path) -> None:
    """A structured result, through the output tool every route uses for one."""
    rig = _Rig(tmp_path)
    prompt = "Record that the fisheries clause was settled sixty-forty."
    live = Agent(model=PinnedModel(model, rig.ledger), output_type=Settlement).run_sync(prompt)
    events = rig.commit()

    replay = rig.replay(events)
    again = Agent(model=PinnedModel(model, replay), output_type=Settlement).run_sync(prompt)

    assert again.output == live.output
    assert replay.divergence is None


@dataclass
class _LookUpFirst(AbstractCapability[None]):
    """
    Force the ``lookup`` tool on step one, and nothing after.

    pydantic-ai refuses a *static* forced ``tool_choice`` because it would apply to every
    step and the agent could never finish; per-step settings from a capability are its
    sanctioned way to force only the first. Without the forcing, a model that answered
    from memory would make this check pass having tested nothing multi-step at all.
    """

    def get_model_settings(self) -> AgentModelSettings[None]:
        def per_step(ctx: RunContext[None]) -> ModelSettings:
            return {"tool_choice": ["lookup"]} if ctx.run_step == 1 else {}

        return per_step


def check_tool(model: Model, tmp_path: Path) -> None:
    """
    A forced tool call, its result, and a final answer — then all of it from the log.

    The tool is pinned, so the replay must not run it either: a replay that reproduced
    what the agent *said* while re-running what it *did* is not a replay.
    """
    runs: list[str] = []

    def lookup(clause: str) -> str:
        """How a clause was settled at the last summit."""
        runs.append(clause)
        return "sixty-forty in favour of the coastal state"

    def agent(resolver: PinResolver) -> Agent[None, str]:
        return Agent(
            model=PinnedModel(model, resolver),
            deps_type=type(None),
            tools=[pinned_tool(resolver, lookup)],
            capabilities=[_LookUpFirst()],
        )

    rig = _Rig(tmp_path)
    prompt = "How was the fisheries clause settled last time? Look it up, then say."
    live = agent(rig.ledger).run_sync(prompt).output
    events = rig.commit()
    assert runs, "the tool ran on the live call -- forced, so this cannot be the model's whim"
    assert _pins(events, "model_call") >= 2, "at least the tool request and the answer"
    ran_live = len(runs)

    replay = rig.replay(events)
    again = agent(replay).run_sync(prompt).output

    assert again == live
    assert len(runs) == ran_live, "the replay ran the tool again"
    assert replay.divergence is None, (
        "request two contains request one's response; if a real response does not "
        "survive the pin's round trip, this is where its key moves"
    )


def check_stream(model: Model, tmp_path: Path) -> None:
    """A streamed call: the accumulated response pinned, a synthetic stream replayed."""
    prompt = "Count from one to five, in words."

    async def stream(agent: Agent[None, str]) -> tuple[str, int]:
        pieces = 0
        async with agent.run_stream(prompt) as result:
            async for _ in result.stream_text(delta=True):
                pieces += 1
            return await result.get_output(), pieces

    rig = _Rig(tmp_path)
    live, delivered = asyncio.run(stream(Agent(model=PinnedModel(model, rig.ledger))))
    events = rig.commit()
    assert delivered > 0, "it arrived as a stream"

    replay = rig.replay(events)
    again, _ = asyncio.run(stream(Agent(model=PinnedModel(model, replay))))

    assert again == live
    assert replay.divergence is None


#: Every check, by name, for parametrizing.
CHECKS = {
    "plain": check_plain,
    "structured": check_structured,
    "tool": check_tool,
    "stream": check_stream,
}
