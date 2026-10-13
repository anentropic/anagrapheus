"""A batch whose ``apply`` fails part-way is retried whole, losing no unit."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from anagrapheus.chronicle.sections import Section

    from .conftest import Rig


class ApplyFailed(Exception):
    """The injected failure."""


def fail_once_at(rig: Rig, monkeypatch: pytest.MonkeyPatch, to_seq: int) -> None:
    """Make writing the section that ends at ``to_seq`` fail, the first time only."""
    real = rig.chronicle.append
    armed = [True]

    def append(section: Section) -> None:
        if armed[0] and section.anchor.to_seq == to_seq:
            armed[0] = False
            raise ApplyFailed(f"apply failed at seq {to_seq}")
        real(section)

    monkeypatch.setattr(rig.chronicle, "append", append)


def catch_up(rig: Rig, mode: str) -> Callable[[], object]:
    if mode == "sync":
        return rig.projector.catch_up
    return lambda: asyncio.run(rig.projector.catch_up_async())


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("failing_boundary", [2, 4, 6])
def test_retry_after_apply_failure_keeps_every_unit(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, mode: str, failing_boundary: int
) -> None:
    # One batch, three units: 1-2, 3-4, 5-6.
    rig.append(
        rig.step("a"),
        rig.close("one"),
        rig.step("b"),
        rig.close("two"),
        rig.step("c"),
        rig.close("three"),
    )
    fail_once_at(rig, monkeypatch, failing_boundary)
    run = catch_up(rig, mode)

    with pytest.raises(ApplyFailed):
        run()
    assert rig.chronicle.sections() == []  # the whole batch rolled back
    assert rig.summarizer.calls == [(1, 2), (3, 4), (5, 6)]

    run()

    anchors = [(s.anchor.from_seq, s.anchor.to_seq) for s in rig.chronicle.sections()]
    assert anchors == [(1, 2), (3, 4), (5, 6)]
    # Nothing the failed batch drew was written, so every unit is drawn again.
    assert rig.summarizer.calls == [(1, 2), (3, 4), (5, 6)] * 2
    assert all(s.prose.startswith(("[g4]", "[g5]", "[g6]")) for s in rig.chronicle.sections())
