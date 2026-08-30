"""
The chronicle's storage port.

Append-only by contract, not merely by convention: there is no update and no delete.
Doc 00 §7 wants supersession rather than correction — *"an old section is never wrong,
only superseded by time"* — so a re-consolidation writes a new row pointing at the old
one, and the old prose stays readable.

**Not disposable like the other projections.** The belief index and the identity
registry can be emptied and re-folded for free. This one cannot: re-folding it re-runs
every model call it ever made and returns the same anchors with different prose. Hence
:meth:`export` and :meth:`load` — the carry path, which no other projection needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from anagrapheus.chronicle.sections import Anchor, Section, SectionId

__all__ = ["ChronicleStore"]


class ChronicleStore(Protocol):
    """The storage the chronicle reads, and its fold appends to."""

    def append(self, section: Section) -> None:
        """Add a section. Never updates: a correction supersedes rather than edits."""
        ...

    def anchors(self, *, upto_seq: int | None = None) -> Sequence[Anchor]:
        """
        Every current anchor, oldest first, superseded ones resolved away.

        The deterministic surface. Safe for a binding read — a gate, a validator, a
        decision about what to consolidate next — because a rebuild reproduces it.
        """
        ...

    def section(self, section_id: SectionId) -> Section | None:
        """One section by id, superseded or not."""
        ...

    def covering(self, seq: int) -> Section | None:
        """The current section covering ``seq``, if any range does."""
        ...

    def sections(self, *, upto_seq: int | None = None) -> Sequence[Section]:
        """Every current section, oldest first. Carries prose: advisory reads only."""
        ...

    def consolidated_through(self) -> int:
        """
        The highest ``to_seq`` any section covers, or 0.

        Where the consolidated past ends and the verbatim tail begins. An anchor read,
        so it is deterministic.
        """
        ...

    def export(self) -> Sequence[Section]:
        """Every row, superseded included — the carry path's read half."""
        ...

    def load(self, sections: Iterable[Section]) -> int:
        """
        Insert exported sections, skipping ids already present. Returns how many landed.

        **Resumable transfer of one chronicle, not a merge.** Idempotent, so a carry
        interrupted half-way can simply be repeated. But section ids are derived from
        the range they cover — which is what makes anchors replay — so two chronicles
        over the same log collide on every id, and a collision keeps what is already
        there and drops the incoming prose.

        That is fine for the case this exists for: carrying *a* chronicle across a
        backend swap. It is not a merge, and an earlier version of this docstring
        claimed it was. Merging divergent consolidations would need identity that
        distinguishes them, which nothing here provides — see note 05 §8.
        """
        ...

    def reset(self) -> None:
        """Drop every section. Only reached through an acknowledged rebuild."""
        ...
