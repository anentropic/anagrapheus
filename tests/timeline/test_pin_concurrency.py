"""
Concurrent draws on one key, which the async path made possible.

`resolve` has no await between checking the scratch and writing to it, so within a
thread it is atomic. `resolve_async` introduced a suspension point in exactly that gap —
and pydantic-ai runs non-barrier tool calls concurrently, so two calls on one key is an
ordinary occurrence rather than a contrived one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    ReplayPins,
    SQLiteEventStore,
    register_builtin_events,
)
from anagrapheus.timeline.pins import pin_key

if TYPE_CHECKING:
    from pathlib import Path

KEY = pin_key("test", {"same": "call"})


def build(path: Path) -> tuple[SQLiteEventStore, EventTypeRegistry, PinLedger]:
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(path)
    return store, types, PinLedger(store, types)


def test_concurrent_draws_on_one_key_run_the_effect_once(tmp_path: Path):
    """
    The promise the whole layer rests on: the effect happens once.

    Both callers must also get the same answer — a draw that ran twice and kept the
    second result would be a different run wearing the first one's key.
    """
    store, _types, ledger = build(tmp_path / "a.db")
    fired: list[int] = []

    async def effect() -> str:
        fired.append(1)
        await asyncio.sleep(0.01)  # a real effect suspends; that is the whole problem
        return f"draw {len(fired)}"

    async def race() -> list[str]:
        return await asyncio.gather(
            *[
                ledger.resolve_async(KEY, kind="test", effect=effect, interpret=lambda raw: raw)
                for _ in range(5)
            ]
        )

    try:
        results = asyncio.run(race())
    finally:
        store.close()

    assert fired == [1]
    assert results == ["draw 1"] * 5


def test_concurrent_draws_on_different_keys_still_run_in_parallel(tmp_path: Path):
    """
    Coalescing must be per key, not a global lock.

    Serializing every draw would make an agent's concurrent tool calls sequential — a
    correctness fix that quietly became a performance regression.
    """
    store, _types, ledger = build(tmp_path / "a.db")
    running = 0
    peak = 0

    async def effect() -> str:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return "done"

    async def race() -> list[str]:
        return await asyncio.gather(
            *[
                ledger.resolve_async(
                    pin_key("test", {"n": n}), kind="test", effect=effect, interpret=lambda r: r
                )
                for n in range(4)
            ]
        )

    try:
        asyncio.run(race())
    finally:
        store.close()

    assert peak == 4


def test_replay_record_mode_coalesces_too(tmp_path: Path):
    """Record mode pins a fresh draw, so it has the same gap and needs the same fix."""
    store, types, ledger = build(tmp_path / "a.db")
    try:
        replay = ReplayPins.from_events(list(store.read()), types, on_miss="record", into=ledger)
        fired: list[int] = []

        async def effect() -> str:
            fired.append(1)
            await asyncio.sleep(0.01)
            return "drawn"

        async def race() -> list[str]:
            return await asyncio.gather(
                *[
                    replay.resolve_async(KEY, kind="test", effect=effect, interpret=lambda r: r)
                    for _ in range(3)
                ]
            )

        assert asyncio.run(race()) == ["drawn"] * 3
        assert fired == [1]
    finally:
        store.close()


@pytest.mark.parametrize("count", [2, 8])
def test_the_scratch_never_sees_a_duplicate_insert(tmp_path: Path, count: int):
    """
    The symptom that surfaced first: the second writer hit the primary key.

    Worth asserting separately from the effect count, because a fix that swallowed the
    IntegrityError would satisfy one and not the other.
    """
    store, _types, ledger = build(tmp_path / "a.db")

    async def effect() -> str:
        await asyncio.sleep(0.005)
        return "body"

    async def race() -> list[str]:
        return await asyncio.gather(
            *[
                ledger.resolve_async(KEY, kind="test", effect=effect, interpret=lambda r: r)
                for _ in range(count)
            ]
        )

    try:
        assert asyncio.run(race()) == ["body"] * count
        rows = store.scratch_list(ledger.unit_id)
        assert len(rows) == 1
        assert rows[0].attempts == count
    finally:
        store.close()
