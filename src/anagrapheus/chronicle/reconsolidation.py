"""
Re-consolidating a range the chronicle already covers.

The fold consolidates each range once. Writing a better account of a range later, with
an edited prompt, say, is a deliberate act of the application's, and this is its entry
point: it reads the range again from the log, draws a new consolidation, and writes it
as the next generation, superseding the current section. The old prose stays readable
by id.

**Outside the anchor-replay guarantee.** Nothing in the log records that a
re-consolidation happened, so no fold can derive it: rebuilding the chronicle discards
it and returns generation 1, and only carrying the chronicle keeps it. And because the
new prose replaces the old in every later read, a run that read the old prose will not
replay against the new. That is why every call logs a warning naming the section: the
divergence is announced, never silent.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from anagrapheus.chronicle.errors import ChronicleFork, UnknownSection
from anagrapheus.chronicle.fold import section_id as derive_section_id
from anagrapheus.chronicle.sections import Anchor, Section

if TYPE_CHECKING:
    from anagrapheus.chronicle.fold import AsyncSummarizer, Consolidation, Summarizer
    from anagrapheus.chronicle.sections import SectionId
    from anagrapheus.chronicle.store import ChronicleStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.upcast import EventReader

__all__ = ["reconsolidate", "reconsolidate_async"]

_log = logging.getLogger(__name__)


def reconsolidate(
    store: ChronicleStore,
    reader: EventReader,
    section_id: SectionId,
    summarize: Summarizer,
) -> Section:
    """
    Write a new consolidation of the range ``section_id`` covers, superseding it.

    Reads the range from the log, calls ``summarize`` on it, and appends the result as
    the next generation of that range, through the store's strict path.

    Raises:
        UnknownSection: ``section_id`` names no section.
        ChronicleFork: ``section_id`` is already superseded. Re-consolidate the current
            section for that range instead; superseding an old one would leave two
            current sections over one range.
        SectionCollision: the next generation's id is already stored.
        TypeError: ``summarize`` returned an awaitable. Use
            :func:`reconsolidate_async` for an async summarizer.
    """
    current, events = _range(store, reader, section_id)
    drawn = summarize(events)
    if inspect.isawaitable(drawn):
        if inspect.iscoroutine(drawn):
            drawn.close()  # never started, so nothing was drawn
        raise TypeError("the summarizer is async; use reconsolidate_async()")
    return _write(store, current, drawn)


async def reconsolidate_async(
    store: ChronicleStore,
    reader: EventReader,
    section_id: SectionId,
    summarize: Summarizer | AsyncSummarizer,
) -> Section:
    """
    The awaited twin of :func:`reconsolidate`, for an async summarizer.

    Takes either kind: an async summarizer is awaited, a synchronous one is called.
    Nothing is written until the draw returns, and the write happens on the calling
    thread, so the store never leaves the thread that opened it. Raises as
    :func:`reconsolidate` does, apart from the ``TypeError``.
    """
    current, events = _range(store, reader, section_id)
    drawn = summarize(events)
    if inspect.isawaitable(drawn):
        drawn = await drawn
    return _write(store, current, drawn)


def _range(
    store: ChronicleStore, reader: EventReader, section_id: SectionId
) -> tuple[Anchor, list[StoredEvent]]:
    """The current anchor ``section_id`` names, and its events. Refuses before any draw."""
    found = store.section(section_id)
    if found is None:
        raise UnknownSection(f"no section {section_id!r}")
    anchor = found.anchor
    head = store.covering(anchor.from_seq)
    if head is None or head.anchor.section_id != section_id:
        current = f"; the current one is {head.anchor.section_id!r}" if head else ""
        raise ChronicleFork(f"section {section_id!r} is already superseded{current}")
    events = list(
        reader.read_raw(since_seq=anchor.from_seq - 1, limit=anchor.to_seq - anchor.from_seq + 1)
    )
    return anchor, events


def _write(store: ChronicleStore, current: Anchor, drawn: Consolidation) -> Section:
    """Append ``drawn`` as the generation after ``current``, and announce the divergence."""
    section = Section(
        anchor=Anchor(
            section_id=derive_section_id(
                current.from_seq, current.to_seq, generation=_generation(store, current) + 1
            ),
            from_seq=current.from_seq,
            to_seq=current.to_seq,
            unit=current.unit,
            supersedes=current.section_id,
        ),
        prose=drawn.prose,
        pins=drawn.pins,
    )
    store.append_new(section)
    _log.warning(
        "re-consolidated %s as %s (seq %d..%d): runs that read the old prose will "
        "diverge on replay, and a rebuild of the chronicle discards the new section",
        current.section_id,
        section.anchor.section_id,
        current.from_seq,
        current.to_seq,
    )
    return section


def _generation(store: ChronicleStore, anchor: Anchor) -> int:
    """How many sections the chain ending at ``anchor`` holds: 1 for a fold's own."""
    generation = 1
    supersedes = anchor.supersedes
    while supersedes is not None:
        generation += 1
        previous = store.section(supersedes)
        supersedes = previous.anchor.supersedes if previous is not None else None
    return generation
