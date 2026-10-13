"""
A chronicle section: prose over a cited range of the log.

The chronicle's unit — *"consolidate ranges of the log into prose sections that cite the
event range they cover"* — with the citation as the load-bearing half.

**A section is a projection row, not an event.** It is derived from canon and is never
authoritative, so it does not belong in the log it projects from; putting it there
would make canon contain its own derivation. That is the same reason it carries no
authority when carried across a backend swap: a carried chronicle and a rebuilt one are
equally valid, because neither is canon.

**Two fields at two grades**, and the split is the point (see
:mod:`anagrapheus.chronicle.reading`):

- ``covers``, ``supersedes``, ``unit`` — the **anchors**. Derived from the log alone,
  so they replay exactly.
- ``prose`` — **generated**. The same range consolidated twice gives different words.
  Nothing that settles anything may read it.
"""

# Design: note 05; upstream agent-core doc 00 §7.

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from anagrapheus.timeline.pins import PinKey

__all__ = ["Anchor", "Section", "SectionId"]

type SectionId = str


class Anchor(NamedTuple):
    """
    The deterministic half of a section: what it covers, and what it replaced.

    Everything here is a pure function of the log, so a rebuild reproduces it exactly.
    This is the surface anything is allowed to depend on.
    """

    section_id: SectionId
    #: Inclusive event-sequence range this section consolidates.
    from_seq: int
    to_seq: int
    #: The unit boundary that triggered consolidation — the marker event's seq.
    unit: int
    #: The section this one supersedes, if it is a re-consolidation of the same range.
    supersedes: SectionId | None


class Section(NamedTuple):
    """An anchor and the prose written over it."""

    anchor: Anchor
    prose: str
    #: The pins the consolidating draws resolved under — plural, since a range may take
    #: several calls. Present so a rebuild can be *shown* to have hit rather than
    #: assumed to have; empty for a section written outside a ledger.
    pins: tuple[PinKey, ...] = ()
