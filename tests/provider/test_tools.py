"""Pinned tools: the world is touched once, and replay does not touch it again."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from anagrapheus.provider import TOOL_PIN_KIND, pinned_tool
from anagrapheus.timeline import ReplayPins

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from .conftest import Rig


def test_the_side_effect_happens_once_across_a_run_and_its_replay(rig: Rig):
    """
    The reason tools are pinned at all.

    Pinning only the model half gives a replay that reproduces what the agent *said*
    while re-running what it *did*: the fetch repeats, the row is written twice. Here
    the effect is a list append, and it must be appended to exactly once.
    """
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return "the body"

    live = pinned_tool(rig.ledger, fetch)
    assert asyncio.run(live("http://example.test")) == "the body"
    assert asyncio.run(live("http://example.test")) == "the body"
    rig.ledger.commit()

    replay = ReplayPins.from_events(list(rig.store.read()), rig.types, on_miss="strict")
    replayed = pinned_tool(replay, fetch)

    assert asyncio.run(replayed("http://example.test")) == "the body"
    assert fetched == ["http://example.test"]


def test_different_arguments_are_different_calls(rig: Rig):
    """Reuse is per call, not per tool: another url is another fetch."""
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return url.upper()

    pinned = pinned_tool(rig.ledger, fetch)
    asyncio.run(pinned("a"))
    asyncio.run(pinned("b"))
    asyncio.run(pinned("a"))

    assert fetched == ["a", "b"]


def test_an_async_tool_is_awaited_only_on_a_miss(rig: Rig):
    """
    A reused pin costs no call at all, rather than one started and thrown away.

    Worth asserting separately: the natural way to write the async path — await first,
    then check the pin — would still perform the effect every time.
    """
    calls: list[int] = []

    async def fetch(n: int) -> int:
        calls.append(n)
        return n * 2

    pinned = pinned_tool(rig.ledger, fetch)

    assert asyncio.run(pinned(21)) == 42
    assert asyncio.run(pinned(21)) == 42
    assert calls == [21]


def test_the_wrapper_keeps_the_tool_signature(rig: Rig):
    """
    pydantic-ai builds the tool schema from the signature, so it has to survive.

    A wrapper that erased it into ``*args, **kwargs`` would still run, and the model
    would be told the tool takes anything — the kind of break that shows up as bad
    answers rather than as an error.
    """

    def lookup(what: str, limit: int = 5) -> str:
        return f"{what}:{limit}"  # pragma: no cover -- the signature is the subject

    pinned = pinned_tool(rig.ledger, lookup)

    assert inspect.signature(pinned) == inspect.signature(lookup)
    assert pinned.__name__ == "lookup"


def test_the_pin_records_the_tool_kind(rig: Rig):
    """A tool draw is distinguishable from a model draw in the log."""

    def fetch() -> str:
        return "body"

    asyncio.run(pinned_tool(rig.ledger, fetch)())
    rig.ledger.commit()

    promoted = [e for e in rig.store.read() if e.envelope.type == "PinPromoted"]
    assert [e.payload["kind"] for e in promoted] == [TOOL_PIN_KIND]


def test_a_result_that_cannot_be_recorded_fails_at_the_draw(rig: Rig):
    """
    A pin that could not be stored is a pin that would not replay.

    Failing here rather than at commit is the right moment: the run finds out while it
    can still do something about it, not after the work is done.
    """

    class Opaque:
        pass

    def build() -> object:
        return Opaque()

    with pytest.raises(TypeError):
        asyncio.run(pinned_tool(rig.ledger, build)())


def test_a_tool_taking_run_context_excludes_it_from_the_key(rig: Rig):
    """
    The context is filled from the run, not by the model, and differs between runs.

    Keying on it would make every replay a miss — the same failure as keying on a
    timestamp, arriving through the tool path.
    """
    calls: list[str] = []

    def lookup(ctx: object, what: str) -> str:
        calls.append(what)
        return "Portugal"

    pinned = pinned_tool(rig.ledger, lookup)

    assert asyncio.run(pinned(object(), "capital")) == "Portugal"
    assert asyncio.run(pinned(object(), "capital")) == "Portugal"
    assert calls == ["capital"]


def test_the_injected_context_is_found_by_annotation_not_by_name(rig: Rig):
    """
    pydantic-ai injects the context by the first parameter's ``RunContext`` annotation.

    A name list gets it wrong in both directions: a context named ``state`` leaks
    per-run state into the key so every replay misses, and a genuine model argument
    named ``context`` is dropped so two different calls collide.
    """
    calls: list[str] = []

    def lookup(state: RunContext[None], what: str) -> str:
        calls.append(what)
        return "Portugal"

    pinned = pinned_tool(rig.ledger, lookup)
    # Deliberately distinguishable: two runs never share a context, so a wrapper that
    # keyed on it would miss every time and this test would catch nothing if they
    # happened to look alike.
    first = _context(run_id="run-one", deps=None)
    second = _context(run_id="run-two", deps=None)

    assert asyncio.run(pinned(first, "capital")) == "Portugal"
    assert asyncio.run(pinned(second, "capital")) == "Portugal"
    assert calls == ["capital"]


def test_an_argument_merely_called_context_is_kept(rig: Rig):
    """
    The other direction, and the one that answers wrongly rather than merely re-drawing.

    ``context`` here is an ordinary argument the model supplies. Dropping it made two
    different questions one call, so the second got the first one's answer.
    """
    asked: list[tuple[str, str]] = []

    def search(context: str, query: str) -> str:
        asked.append((context, query))
        return f"{context}:{query}"

    pinned = pinned_tool(rig.ledger, search)

    assert asyncio.run(pinned("europe", "q")) == "europe:q"
    assert asyncio.run(pinned("asia", "q")) == "asia:q"
    assert asked == [("europe", "q"), ("asia", "q")]


def test_a_plain_callable_returning_an_awaitable_is_awaited(rig: Rig):
    """
    pydantic-ai supports a tool that is not a coroutine function but returns a coroutine.

    ``iscoroutinefunction`` is false for it, so the wrapper would have tried to record
    the coroutine object and failed — changing the tool's behaviour by wrapping it.
    """
    calls: list[int] = []

    def returns_coroutine(n: int):  # noqa: ANN202 -- the untyped shape is the subject
        async def inner() -> int:
            calls.append(n)
            return n * 2

        return inner()

    pinned = pinned_tool(rig.ledger, returns_coroutine)

    assert asyncio.run(pinned(21)) == 42
    assert asyncio.run(pinned(21)) == 42
    assert calls == [21]


def _context(**kwargs: object) -> RunContext[None]:
    """A RunContext with only what the wrapper is allowed to look at: nothing."""
    return cast("RunContext[None]", SimpleNamespace(**kwargs))
