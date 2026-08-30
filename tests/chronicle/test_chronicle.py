"""The anchoring discipline: supersession, the recency/depth split, and the grades."""

from __future__ import annotations

from typing import TYPE_CHECKING

from anagrapheus.chronicle import (
    Anchors,
    Prose,
    Section,
    section_id,
    substring_retriever,
)
from anagrapheus.chronicle.sections import Anchor

if TYPE_CHECKING:
    from .conftest import Rig


def test_a_section_cites_the_range_it_covers(rig: Rig):
    """
    Doc 00 §7's first bullet, and the load-bearing half of the whole layer.

    Without the citation a section is an unattributable summary; with it, the log can
    always be consulted for what the prose was written from.
    """
    rig.commit(rig.step("a"), rig.step("b"), rig.close("one"))

    anchor = rig.anchors.all()[0]
    assert (anchor.from_seq, anchor.to_seq) == (1, 3)
    assert rig.summarizer.calls == [(1, 3)], "the summarizer saw exactly that range"


def test_consecutive_units_partition_the_log(rig: Rig):
    """No gap and no overlap: the next range starts where the last one ended, exactly."""
    rig.commit(rig.step("a"), rig.close("one"))
    rig.commit(rig.step("b"), rig.step("c"), rig.close("two"))

    ranges = [(a.from_seq, a.to_seq) for a in rig.anchors.all()]
    assert ranges == [(1, 2), (3, 5)]


def test_an_old_section_is_superseded_never_edited(rig: Rig):
    """
    §7's second bullet, on supersession rather than correction.

    *"An old section is never wrong, only superseded by time."*

    The superseded prose stays readable by id — which is what makes supersession
    different from correction, and what lets you see what a past run actually read.
    """
    rig.commit(rig.step("a"), rig.close("one"))
    original = rig.anchors.all()[0]

    rig.chronicle.append(
        Section(
            anchor=Anchor(
                section_id=section_id(1, original.to_seq, generation=2),
                from_seq=1,
                to_seq=original.to_seq,
                unit=original.unit,
                supersedes=original.section_id,
            ),
            prose="the later account",
        )
    )

    assert [a.section_id for a in rig.anchors.all()] == [f"1-{original.to_seq}#2"]
    superseded = rig.prose.section(original.section_id)
    assert superseded is not None
    assert superseded.prose.startswith("[g1]"), "the old prose survives, unedited"


def test_the_tail_comes_from_the_log_not_the_chronicle(rig: Rig):
    """
    §7's third bullet, and one of R61 clause 4's by-construction guarantees.

    Near-term continuity never reads generated prose: the tail is the log's own events.
    This is why consolidation lag is invisible rather than a gap — whatever the
    chronicle has not reached yet, the tail is already covering.
    """
    rig.commit(rig.step("a"), rig.close("one"))
    rig.append(rig.step("b"), rig.step("c"))  # deliberately not consolidated

    through = rig.anchors.consolidated_through()
    tail = rig.brief.tail(since_seq=through)

    assert [e.payload["what"] for e in tail.events] == ["b", "c"]
    assert rig.summarizer.calls == [(1, 2)], "the tail cost no consolidation"


def test_consolidation_lag_self_heals(rig: Rig):
    """
    The watermark does the work: a chronicle running behind catches up on its own.

    Worth asserting because it is the reason consolidation can be *debt* — deferred
    without a reconciliation step, and without anything downstream noticing.
    """
    rig.append(rig.step("a"), rig.close("one"))
    rig.append(rig.step("b"), rig.close("two"))
    assert list(rig.anchors.all()) == []

    rig.projector.catch_up()

    assert [(a.from_seq, a.to_seq) for a in rig.anchors.all()] == [(1, 2), (3, 4)]


def test_the_anchor_surface_cannot_reach_prose(rig: Rig):
    """
    R61 clause 4 enforced by the API rather than by a docstring.

    A caller that needs to settle something takes ``Anchors`` and finds there is simply
    no prose on it to misuse. A rule that lives only in prose is a rule that gets
    forgotten under deadline.
    """
    rig.commit(rig.step("a"), rig.close("one"))
    anchors = Anchors(rig.chronicle)

    assert not hasattr(anchors, "prose")
    assert not any("prose" in field for field in rig.anchors.all()[0]._fields)
    assert hasattr(Prose(rig.chronicle), "all")


def test_covering_finds_the_section_for_an_event(rig: Rig):
    """The lookup a retrieval step starts from: which section speaks for this event."""
    rig.commit(rig.step("a"), rig.step("b"), rig.close("one"))
    rig.commit(rig.step("c"), rig.close("two"))

    found = rig.prose.covering(2)
    assert found is not None
    assert (found.anchor.from_seq, found.anchor.to_seq) == (1, 3)
    assert rig.prose.covering(99) is None


def test_the_default_retriever_is_naive_and_says_so(rig: Rig):
    """
    A port with a placeholder behind it, not a search engine.

    §7 calls the real stack commodity that belongs behind a port. The default exists so
    the layer is usable and testable; it ranks by recency and matches by substring, and
    the test asserts exactly that rather than pretending to more.
    """
    rig.commit(rig.step("apples"), rig.close("one"))
    rig.commit(rig.step("oranges"), rig.close("two"))
    rig.commit(rig.step("apples again"), rig.close("three"))

    hits = substring_retriever(rig.prose.all(), "apples")

    assert len(hits) == 2
    assert hits[0].anchor.to_seq > hits[1].anchor.to_seq, "most recent first"
    assert substring_retriever(rig.prose.all(), "pears") == []
