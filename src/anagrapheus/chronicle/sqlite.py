"""
The chronicle adapter: the only module in this subpackage containing SQL.

Shares the timeline's connection, and therefore its file and its transactions — the
same arrangement :mod:`anagrapheus.identity.sqlite` and
:mod:`anagrapheus.beliefs.sqlite` use, so a section and the fold position that produced
it commit together.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from anagrapheus.chronicle.errors import ChronicleFork, SectionCollision, UnknownSection
from anagrapheus.chronicle.migrations import COMPONENT, MIGRATIONS
from anagrapheus.chronicle.sections import Anchor, Section
from anagrapheus.timeline.migrations import migrate

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence

    from anagrapheus.chronicle.sections import SectionId

__all__ = ["SQLiteChronicleStore"]

_COLUMNS = "section_id, from_seq, to_seq, unit, supersedes, prose, pins"


class SQLiteChronicleStore:
    """Implements ``ChronicleStore`` over the same file the timeline uses."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, COMPONENT, MIGRATIONS)

    # ---- writes ---------------------------------------------------------------------

    def append(self, section: Section) -> None:
        """Add a section, keeping the existing row on an id collision. Refuses a fork."""
        # Keep rather than overwrite on a collision, unlike the sibling adapters. The
        # fold contract allows the last batch to be re-applied after a crash, and a
        # section id is derived from its range -- so a replayed batch must find the
        # existing row and leave its prose alone. Overwriting would silently swap one
        # valid consolidation for another, which is the one thing append-only forbids.
        with self._atomic():
            self._insert(section, strict=False)

    def append_new(self, section: Section) -> None:
        """Add a section that must not already exist. Refuses a collision and a fork."""
        with self._atomic():
            self._insert(section, strict=True)

    def load(self, sections: Iterable[Section]) -> int:
        """Insert exported sections, skipping ids already present. Transfer, not merge."""
        with self._atomic():
            return sum(self._insert(s, strict=False) for s in _supersession_order(sections))

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
        """
        The current section covering ``seq``, if any range does.

        Both predicates in SQL, and one row back. Filtering in Python would load every
        section's prose to answer a point lookup, growing linearly with the chronicle
        and bypassing the range index that exists for exactly this read.
        """
        row = self._conn.execute(
            f"""
            SELECT {_COLUMNS} FROM chronicle_sections
             WHERE from_seq <= :seq AND to_seq >= :seq
               AND section_id NOT IN (
                       SELECT supersedes FROM chronicle_sections WHERE supersedes IS NOT NULL
                   )
             ORDER BY from_seq
             LIMIT 1
            """,  # noqa: S608 -- `_COLUMNS` is a module literal
            {"seq": seq},
        ).fetchone()
        return _row(row) if row is not None else None

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

    @contextmanager
    def _atomic(self) -> Generator[None]:
        """
        Make a check and the insert it guards one unit.

        Joins a transaction already open on the shared connection -- the projector's,
        when the fold appends -- as a savepoint, and otherwise takes the write lock
        before reading, so no other connection can slip a section in between.
        """
        if self._conn.in_transaction:
            self._conn.execute("SAVEPOINT anagrapheus_chronicle")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK TO anagrapheus_chronicle")
                self._conn.execute("RELEASE anagrapheus_chronicle")
                raise
            self._conn.execute("RELEASE anagrapheus_chronicle")
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _insert(self, section: Section, *, strict: bool) -> bool:
        """Insert one section unless its id is stored; refuse a fork. Whether it landed."""
        anchor = section.anchor
        if self.section(anchor.section_id) is not None:
            # Checked before the fork guard: a replayed fold section whose range has
            # since been re-consolidated would otherwise look like an overlap.
            if strict:
                raise SectionCollision(f"section {anchor.section_id!r} is already stored")
            return False
        if anchor.supersedes is not None:
            if self.section(anchor.supersedes) is None:
                raise UnknownSection(
                    f"{anchor.section_id!r} supersedes {anchor.supersedes!r}, "
                    "which is not in the store"
                )
            successor = self._conn.execute(
                "SELECT section_id FROM chronicle_sections WHERE supersedes = ? LIMIT 1",
                (anchor.supersedes,),
            ).fetchone()
            if successor is not None:
                raise ChronicleFork(
                    f"{anchor.section_id!r} supersedes {anchor.supersedes!r}, which "
                    f"{successor['section_id']!r} already supersedes"
                )
        overlap = self._conn.execute(
            """
            SELECT section_id FROM chronicle_sections
             WHERE from_seq <= :to AND to_seq >= :from
               AND section_id IS NOT :supersedes
               AND section_id NOT IN (
                       SELECT supersedes FROM chronicle_sections WHERE supersedes IS NOT NULL
                   )
             ORDER BY from_seq
             LIMIT 1
            """,
            {"from": anchor.from_seq, "to": anchor.to_seq, "supersedes": anchor.supersedes},
        ).fetchone()
        if overlap is not None:
            raise ChronicleFork(
                f"{anchor.section_id!r} covers {anchor.from_seq}..{anchor.to_seq}, which "
                f"overlaps current section {overlap['section_id']!r} without superseding it"
            )
        self._conn.execute(
            f"INSERT INTO chronicle_sections ({_COLUMNS}) "  # noqa: S608 -- a literal
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                anchor.section_id,
                anchor.from_seq,
                anchor.to_seq,
                anchor.unit,
                anchor.supersedes,
                section.prose,
                _dump(section.pins),
            ),
        )
        return True

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


def _supersession_order(sections: Iterable[Section]) -> list[Section]:
    """
    ``sections`` reordered so each lands after the one it supersedes.

    An export is ordered by range and id, and ids sort as text: generation 10 of a range
    sorts before generation 2, which supersedes nothing it could follow. Otherwise
    stable, so an export that is already in order loads in that order.
    """
    pending = list(sections)
    ordered: list[Section] = []
    while pending:
        waiting = {s.anchor.section_id for s in pending}
        ready = [s for s in pending if s.anchor.supersedes not in waiting]
        if not ready:
            raise ChronicleFork("these sections supersede each other in a cycle")
        ordered.extend(ready)
        pending = [s for s in pending if s.anchor.supersedes in waiting]
    return ordered


def _dump(pins: tuple[str, ...]) -> str:
    """Pins as one text column. Newline-joined: a pin key never contains one."""
    return "\n".join(pins)


def _parse(raw: str) -> tuple[str, ...]:
    """The inverse of :func:`_dump`, treating empty as no pins rather than one blank."""
    return tuple(raw.split("\n")) if raw else ()


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
        pins=_parse(row["pins"]),
    )
