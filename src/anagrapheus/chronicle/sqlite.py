"""
The chronicle adapter: the only module in this subpackage containing SQL.

Shares the timeline's connection, and therefore its file and its transactions — the
same arrangement :mod:`anagrapheus.identity.sqlite` and
:mod:`anagrapheus.beliefs.sqlite` use, so a section and the fold position that produced
it commit together.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from anagrapheus.chronicle.migrations import COMPONENT, MIGRATIONS
from anagrapheus.chronicle.sections import Anchor, Section
from anagrapheus.timeline.migrations import migrate

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from anagrapheus.chronicle.sections import SectionId

__all__ = ["SQLiteChronicleStore"]

_COLUMNS = "section_id, from_seq, to_seq, unit, supersedes, prose, pin"


class SQLiteChronicleStore:
    """Implements ``ChronicleStore`` over the same file the timeline uses."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, COMPONENT, MIGRATIONS)

    # ---- writes ---------------------------------------------------------------------

    def append(self, section: Section) -> None:
        """Add a section. Never updates: a correction supersedes rather than edits."""
        # DO NOTHING rather than DO UPDATE, unlike the sibling adapters. The fold
        # contract allows the last batch to be re-applied after a crash, and a section
        # id is derived from its range -- so a replayed batch must find the existing row
        # and leave its prose alone. Overwriting would silently swap one valid
        # consolidation for another, which is the one thing append-only forbids.
        self._conn.execute(
            f"INSERT INTO chronicle_sections ({_COLUMNS}) "  # noqa: S608 -- a literal
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (section_id) DO NOTHING",
            (
                section.anchor.section_id,
                section.anchor.from_seq,
                section.anchor.to_seq,
                section.anchor.unit,
                section.anchor.supersedes,
                section.prose,
                section.pin,
            ),
        )

    def load(self, sections: Iterable[Section]) -> int:
        """Insert exported sections, skipping ids already present."""
        landed = 0
        for section in sections:
            before = self._conn.total_changes
            self.append(section)
            landed += self._conn.total_changes - before
        return landed

    def reset(self) -> None:
        """Drop every section. Only reached through an acknowledged rebuild."""
        self._conn.execute("DELETE FROM chronicle_sections")

    # ---- anchor reads (deterministic) -----------------------------------------------

    def anchors(self, *, upto_seq: int | None = None) -> Sequence[Anchor]:
        """Every current anchor, oldest first, superseded ones resolved away."""
        return [_row(row).anchor for row in self._current(upto_seq)]

    def consolidated_through(self) -> int:
        """The highest ``to_seq`` any section covers, or 0."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(to_seq), 0) AS through FROM chronicle_sections"
        ).fetchone()
        return int(row["through"])

    # ---- prose reads (generated) ----------------------------------------------------

    def section(self, section_id: SectionId) -> Section | None:
        """One section by id, superseded or not."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM chronicle_sections WHERE section_id = ?",  # noqa: S608
            (section_id,),
        ).fetchone()
        return _row(row) if row is not None else None

    def covering(self, seq: int) -> Section | None:
        """The current section covering ``seq``, if any range does."""
        found = [s for s in self.sections() if s.anchor.from_seq <= seq <= s.anchor.to_seq]
        return found[0] if found else None

    def sections(self, *, upto_seq: int | None = None) -> Sequence[Section]:
        """Every current section, oldest first."""
        return [_row(row) for row in self._current(upto_seq)]

    def export(self) -> Sequence[Section]:
        """Every row, superseded included."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM chronicle_sections ORDER BY from_seq, section_id"  # noqa: S608
        ).fetchall()
        return [_row(row) for row in rows]

    # ---- internals ------------------------------------------------------------------

    def _current(self, upto_seq: int | None) -> Sequence[sqlite3.Row]:
        """
        Rows nothing supersedes, oldest first.

        The head of each supersession chain, found by exclusion rather than by walking:
        a section is current exactly when no other section names it. That holds however
        long the chain grows, and needs no "is_current" column to be kept honest.
        """
        clause = "" if upto_seq is None else "AND to_seq <= :upto"
        return self._conn.execute(
            f"""
            SELECT {_COLUMNS} FROM chronicle_sections
             WHERE section_id NOT IN (
                       SELECT supersedes FROM chronicle_sections WHERE supersedes IS NOT NULL
                   )
               {clause}
             ORDER BY from_seq, section_id
            """,  # noqa: S608 -- `clause` is one of two literals
            {"upto": upto_seq},
        ).fetchall()


def _row(row: sqlite3.Row) -> Section:
    """Rebuild a section from its row."""
    return Section(
        anchor=Anchor(
            section_id=row["section_id"],
            from_seq=row["from_seq"],
            to_seq=row["to_seq"],
            unit=row["unit"],
            supersedes=row["supersedes"],
        ),
        prose=row["prose"],
        pin=row["pin"],
    )
