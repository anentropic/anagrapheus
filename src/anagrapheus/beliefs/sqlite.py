"""
The belief index adapter: the only module in this subpackage containing SQL.

It shares the timeline's connection and therefore its file and its transactions, which
is what lets the index's state and its fold position commit together — the same
arrangement :mod:`anagrapheus.identity.sqlite` uses, and for the same reason.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

from anagrapheus.beliefs.migrations import COMPONENT, MIGRATIONS
from anagrapheus.beliefs.store import BeliefRow
from anagrapheus.timeline.migrations import migrate

if TYPE_CHECKING:
    from anagrapheus.beliefs.events import BeliefMinted

__all__ = ["SQLiteBeliefIndexStore"]

_TABLES = ("belief_entities", "belief_ids")


class SQLiteBeliefIndexStore:
    """Implements ``BeliefIndexStore`` over the same file the timeline uses."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, COMPONENT, MIGRATIONS)

    # ---- writes ---------------------------------------------------------------------

    def minted(self, event: BeliefMinted, seq: int) -> None:
        """Record a minted belief id and the entities it names."""
        # ON CONFLICT rather than a plain insert: the fold contract allows the last
        # batch to be re-applied after a crash, and minting the same id twice is
        # ordinary besides -- the same proposition can be asserted in two scopes, or
        # re-asserted later, and minting is deterministic so both produce this row.
        self._conn.execute(
            """
            INSERT INTO belief_ids (belief_id, predicate, seq)
            VALUES (?, ?, ?)
            ON CONFLICT (belief_id) DO UPDATE
                SET predicate = excluded.predicate
            """,
            (event.belief_id, event.predicate, seq),
        )
        for position, entity_id in enumerate(event.entity_ids):
            self._conn.execute(
                """
                INSERT INTO belief_entities (belief_id, entity_id, position, seq)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (belief_id, position) DO UPDATE
                    SET entity_id = excluded.entity_id
                """,
                (event.belief_id, str(entity_id), position, seq),
            )
        # An id re-minted at a shorter arity would leave the extra slots behind, and
        # they would then travel into every re-key. Cheap to prevent, silent if not.
        self._conn.execute(
            "DELETE FROM belief_entities WHERE belief_id = ? AND position >= ?",
            (event.belief_id, len(event.entity_ids)),
        )

    # ---- reads ----------------------------------------------------------------------

    def belief(self, belief_id: str) -> BeliefRow | None:
        """The row for ``belief_id``, or ``None`` if this range never minted it."""
        row = self._conn.execute(
            "SELECT * FROM belief_ids WHERE belief_id = ?", (belief_id,)
        ).fetchone()
        return None if row is None else self._row(row)

    def beliefs_for(self, entity_id: UUID) -> list[BeliefRow]:
        """Every belief id naming ``entity_id``, oldest first."""
        rows = self._conn.execute(
            """
            SELECT b.* FROM belief_ids b
            JOIN belief_entities e ON e.belief_id = b.belief_id
            WHERE e.entity_id = ?
            GROUP BY b.belief_id
            ORDER BY b.seq, b.belief_id
            """,
            (str(entity_id),),
        ).fetchall()
        return [self._row(row) for row in rows]

    def beliefs_of(self, predicate: str) -> list[BeliefRow]:
        """Every belief id minted for ``predicate``, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM belief_ids WHERE predicate = ? ORDER BY seq, belief_id",
            (predicate,),
        ).fetchall()
        return [self._row(row) for row in rows]

    def reset(self) -> None:
        """Empty the projection so it can be rebuilt from the log."""
        for table in _TABLES:
            self._conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- fixed table names

    # ---- internals ------------------------------------------------------------------

    def _row(self, row: sqlite3.Row) -> BeliefRow:
        entities = self._conn.execute(
            "SELECT entity_id FROM belief_entities WHERE belief_id = ? ORDER BY position",
            (row["belief_id"],),
        ).fetchall()
        return BeliefRow(
            belief_id=row["belief_id"],
            predicate=row["predicate"],
            entity_ids=tuple(UUID(e["entity_id"]) for e in entities),
            seq=row["seq"],
        )
