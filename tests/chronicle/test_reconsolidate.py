"""Re-consolidation, and the store guards that stop a chronicle forking."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from anagrapheus.chronicle import (
    Anchor,
    ChronicleFork,
    Consolidation,
    Section,
    SectionCollision,
    UnknownSection,
    reconsolidate,
    reconsolidate_async,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anagrapheus.timeline.envelope import StoredEvent

    from .conftest import Rig


def _consolidated(rig: Rig) -> Anchor:
    """One closed unit, consolidated by the fold: seq 1..2, section ``1-2``."""
    rig.commit(rig.step("a"), rig.close("one"))
    return rig.anchors.all()[0]


def _section(section_id: str, from_seq: int, to_seq: int, supersedes: str | None) -> Section:
    return Section(
        Anchor(section_id, from_seq, to_seq, to_seq, supersedes), f"by hand: {section_id}"
    )


def test_it_appends_the_next_generation_superseding_the_current(rig: Rig):
    """Each call reads the range again, draws once, and extends the chain by one."""
    original = _consolidated(rig)

    second = reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)
    third = reconsolidate(rig.chronicle, rig.reader, second.anchor.section_id, rig.summarizer)

    assert second.anchor == Anchor("1-2#2", 1, 2, original.unit, "1-2")
    assert third.anchor == Anchor("1-2#3", 1, 2, original.unit, "1-2#2")
    assert rig.summarizer.calls == [(1, 2), (1, 2), (1, 2)], "the same range, from the log"
    assert [a.section_id for a in rig.anchors.all()] == ["1-2#3"]
    assert rig.anchors.consolidated_through() == 2
    assert [s.anchor.section_id for s in rig.chronicle.export()] == ["1-2", "1-2#2", "1-2#3"]


def test_it_refuses_a_superseded_section_before_drawing(rig: Rig):
    """Superseding an old generation would fork the range, so it is refused up front."""
    original = _consolidated(rig)
    reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)

    with pytest.raises(ChronicleFork, match=r"'1-2' is already superseded.*'1-2#2'"):
        reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)

    assert len(rig.summarizer.calls) == 2, "no model call was spent on a refusal"
    assert [a.section_id for a in rig.anchors.all()] == ["1-2#2"]


def test_it_refuses_an_unknown_section(rig: Rig):
    _consolidated(rig)
    with pytest.raises(UnknownSection):
        reconsolidate(rig.chronicle, rig.reader, "9-9", rig.summarizer)


def test_a_colliding_id_raises_rather_than_dropping_the_draw(rig: Rig):
    """
    The next generation's id is taken, here by a hand-written section named oddly.

    The fold's keep-the-existing-row rule would drop the new prose without a word.
    """
    original = _consolidated(rig)
    rig.chronicle.append_new(_section("1-2#3", 1, 2, supersedes=original.section_id))

    with pytest.raises(SectionCollision, match="1-2#3"):
        reconsolidate(rig.chronicle, rig.reader, "1-2#3", rig.summarizer)

    assert [a.section_id for a in rig.anchors.all()] == ["1-2#3"]


def test_it_warns_that_replays_will_diverge(rig: Rig, caplog: pytest.LogCaptureFixture):
    original = _consolidated(rig)

    with caplog.at_level(logging.WARNING, logger="anagrapheus.chronicle"):
        reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)

    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.name == "anagrapheus.chronicle.reconsolidation"
    assert "1-2 as 1-2#2" in record.getMessage()
    assert "diverge on replay" in record.getMessage()


def test_an_async_summarizer_is_awaited(rig: Rig):
    original = _consolidated(rig)
    seen: list[tuple[int, int]] = []

    async def summarize(events: Sequence[StoredEvent]) -> Consolidation:
        await asyncio.sleep(0)
        seen.append((events[0].seq, events[-1].seq))
        return Consolidation(prose="awaited", pins=("p1:k",))

    written = asyncio.run(
        reconsolidate_async(rig.chronicle, rig.reader, original.section_id, summarize)
    )

    assert seen == [(1, 2)]
    assert written.anchor.section_id == "1-2#2"
    assert rig.prose.covering(1) == written
    assert written.pins == ("p1:k",)


def test_the_async_variant_takes_a_sync_summarizer_too(rig: Rig):
    original = _consolidated(rig)
    written = asyncio.run(
        reconsolidate_async(rig.chronicle, rig.reader, original.section_id, rig.summarizer)
    )
    assert written.prose.startswith("[g2]")


def test_the_sync_variant_refuses_an_async_summarizer_without_writing(rig: Rig):
    original = _consolidated(rig)

    async def summarize(events: Sequence[StoredEvent]) -> Consolidation:
        raise AssertionError("never awaited")

    # The type checker already rejects this; the runtime check is for untyped callers.
    with pytest.raises(TypeError, match="reconsolidate_async"):
        reconsolidate(rig.chronicle, rig.reader, original.section_id, summarize)  # pyright: ignore[reportArgumentType]

    assert [a.section_id for a in rig.anchors.all()] == [original.section_id]


# ---- the store's guards -------------------------------------------------------------


@pytest.mark.parametrize("strict", [False, True], ids=["append", "append_new"])
def test_the_store_refuses_to_supersede_a_superseded_section(rig: Rig, strict: bool):
    """The hand-rolled fork: two sections both naming ``1-2``, both current."""
    original = _consolidated(rig)
    rig.chronicle.append(_section("1-2#2", 1, 2, supersedes=original.section_id))
    write = rig.chronicle.append_new if strict else rig.chronicle.append

    with pytest.raises(ChronicleFork, match="'1-2#2' already supersedes"):
        write(_section("1-2-other", 1, 2, supersedes=original.section_id))

    assert [a.section_id for a in rig.anchors.all()] == ["1-2#2"]


@pytest.mark.parametrize("strict", [False, True], ids=["append", "append_new"])
def test_the_store_refuses_an_overlap_it_does_not_supersede(rig: Rig, strict: bool):
    _consolidated(rig)
    write = rig.chronicle.append_new if strict else rig.chronicle.append

    with pytest.raises(ChronicleFork, match="overlaps current section '1-2'"):
        write(_section("2-5", 2, 5, supersedes=None))


@pytest.mark.parametrize("strict", [False, True], ids=["append", "append_new"])
@pytest.mark.parametrize(
    ("from_seq", "to_seq"),
    [(2, 2), (1, 1), (1, 5), (0, 2)],
    ids=["narrower", "shorter", "wider", "earlier"],
)
def test_the_store_refuses_a_successor_over_a_different_range(
    rig: Rig, strict: bool, from_seq: int, to_seq: int
):
    """
    A successor that changes the range would strand the old one's coverage.

    The superseded row still counts towards ``consolidated_through``, yet no current
    section would cover the seqs the successor dropped, or every seq it claimed.
    """
    original = _consolidated(rig)
    write = rig.chronicle.append_new if strict else rig.chronicle.append

    with pytest.raises(ChronicleFork, match=r"covers 1\.\.2.*must cover the same range"):
        write(_section("by-hand", from_seq, to_seq, supersedes=original.section_id))

    assert [a.section_id for a in rig.anchors.all()] == ["1-2"]
    assert [s.anchor.section_id for s in rig.chronicle.export()] == ["1-2"]


@pytest.mark.parametrize("strict", [False, True], ids=["append", "append_new"])
def test_the_store_accepts_a_successor_over_the_same_range(rig: Rig, strict: bool):
    original = _consolidated(rig)
    write = rig.chronicle.append_new if strict else rig.chronicle.append

    write(_section("1-2#2", 1, 2, supersedes=original.section_id))

    assert [a.section_id for a in rig.anchors.all()] == ["1-2#2"]
    assert rig.prose.covering(1) == rig.prose.covering(2) == rig.prose.section("1-2#2")


def test_a_load_with_a_successor_over_a_different_range_lands_nothing(rig: Rig):
    original = _consolidated(rig)
    incoming = [_section("3-3", 3, 3, None), _section("2-2", 2, 2, original.section_id)]

    with pytest.raises(ChronicleFork, match="must cover the same range"):
        rig.chronicle.load(incoming)

    assert [s.anchor.section_id for s in rig.chronicle.export()] == ["1-2"]


def test_the_store_refuses_to_supersede_a_missing_section(rig: Rig):
    with pytest.raises(UnknownSection):
        rig.chronicle.append(_section("1-2#2", 1, 2, supersedes="1-2"))


def test_the_strict_path_raises_on_a_collision_and_the_fold_path_keeps_the_row(rig: Rig):
    original = _consolidated(rig)
    stored = rig.prose.section(original.section_id)
    assert stored is not None

    with pytest.raises(SectionCollision):
        rig.chronicle.append_new(stored._replace(prose="different words"))
    rig.chronicle.append(stored._replace(prose="different words"))

    assert rig.prose.section(original.section_id) == stored


def test_fold_replay_stays_idempotent_after_a_reconsolidation(rig: Rig):
    """
    The fold re-appending its own section is not a fork, even once it is superseded.

    The fold contract allows the last batch to be re-applied, and range-derived ids
    mean the replay finds generation 1 already there. The overlap with the current
    generation must not be mistaken for a fork.
    """
    original = _consolidated(rig)
    replayed = rig.prose.section(original.section_id)
    assert replayed is not None
    later = reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)

    rig.chronicle.append(replayed)
    rig.projector.catch_up()

    assert [a.section_id for a in rig.anchors.all()] == [later.anchor.section_id]
    assert len(rig.chronicle.export()) == 2


def test_load_lands_a_chain_in_any_order(rig: Rig):
    """Ids sort as text, so an export can list a later generation before its parent."""
    original = _consolidated(rig)
    second = reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)
    reconsolidate(rig.chronicle, rig.reader, second.anchor.section_id, rig.summarizer)
    carried = list(reversed(rig.chronicle.export()))

    rig.rebuild()
    assert rig.chronicle.load(carried) == 2, "generation 1 was already rebuilt"

    assert [a.section_id for a in rig.anchors.all()] == ["1-2#3"]


def test_a_load_that_would_fork_lands_nothing(rig: Rig):
    _consolidated(rig)
    incoming = [_section("3-3", 3, 3, None), _section("1-5", 1, 5, None)]

    with pytest.raises(ChronicleFork):
        rig.chronicle.load(incoming)

    assert [a.section_id for a in rig.anchors.all()] == ["1-2"]
