"""
The identity adapter: the only module in this subpackage containing SQL.

It shares the timeline's connection and therefore its file and its transactions —
which is what lets the projection's state and its position commit together, so a
crash can never leave the registry ahead of or behind the position that guards it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

from anagrapheus.identity.events import Stance, normalize_surface_form
from anagrapheus.identity.migrations import COMPONENT, MIGRATIONS
from anagrapheus.identity.store import AliasRow, DecisionRow, EntityRow, RekeyRow
from anagrapheus.timeline.migrations import migrate

if TYPE_CHECKING:
    from anagrapheus.identity.events import Aliased, Established, Merged, Split

__all__ = ["SQLiteIdentityStore"]

_TABLES = (
    "identity_rekeys",
    "identity_decisions",
    "identity_aliases",
    "identity_entities",
)


class SQLiteIdentityStore:
    """Implements ``IdentityStore`` over the same file the timeline uses."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, COMPONENT, MIGRATIONS)

    # ---- writes: one per identity role -------------------------------------------

    def established(self, event: Established, seq: int) -> None:
        self._put_entity(event.entity_id, seq, event.provenance)
        self._put_alias(event.entity_id, event.first_surface_form, Stance.CERTAIN, seq)
        self._decision(seq, event.entity_id, "established", {"form": event.first_surface_form})

    def aliased(self, event: Aliased, seq: int) -> None:
        self._put_alias(event.entity_id, event.surface_form, event.stance, seq)
        self._decision(
            seq,
            event.entity_id,
            "aliased",
            {"form": event.surface_form, "stance": event.stance.value},
        )

    def merged(self, event: Merged, seq: int) -> None:
        self._conn.execute(
            "UPDATE identity_entities SET merged_into = ?, merged_seq = ? WHERE entity_id = ?",
            (str(event.kept), seq, str(event.absorbed)),
        )
        self._rekey(seq, "merge", event.absorbed, event.kept)
        detail = {"kept": str(event.kept), "absorbed": str(event.absorbed)}
        self._decision(seq, event.absorbed, "absorbed", detail)
        self._decision(seq, event.kept, "merged", detail)

    def split(self, event: Split, seq: int) -> None:
        origin = self.entity(event.original)
        provenance = origin.provenance if origin is not None else ""
        for assignment in event.into:
            self._put_entity(assignment.entity_id, seq, provenance)
            for form in assignment.surface_forms:
                normalized = normalize_surface_form(form)
                moved = self._conn.execute(
                    "SELECT stance FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
                    (str(event.original), normalized),
                ).fetchone()
                stance = Stance(moved["stance"]) if moved is not None else Stance.SUSPECTED
                self._conn.execute(
                    "DELETE FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
                    (str(event.original), normalized),
                )
                self._put_alias(assignment.entity_id, normalized, stance, seq)
            self._rekey(seq, "split", event.original, assignment.entity_id)
            self._decision(
                seq,
                assignment.entity_id,
                "split_from",
                {"original": str(event.original)},
            )
        self._decision(
            seq,
            event.original,
            "split",
            {"into": [str(a.entity_id) for a in event.into]},
        )

    # ---- reads --------------------------------------------------------------------

    def entity(self, entity_id: UUID) -> EntityRow | None:
        row = self._conn.execute(
            "SELECT * FROM identity_entities WHERE entity_id = ?", (str(entity_id),)
        ).fetchone()
        if row is None:
            return None
        merged_into = row["merged_into"]
        return EntityRow(
            entity_id=UUID(row["entity_id"]),
            established_seq=row["established_seq"],
            provenance=row["provenance"],
            merged_into=None if merged_into is None else UUID(merged_into),
            merged_seq=row["merged_seq"],
        )

    def candidates(self, surface_form: str) -> list[AliasRow]:
        rows = self._conn.execute(
            "SELECT * FROM identity_aliases WHERE surface_form = ? ORDER BY seq",
            (normalize_surface_form(surface_form),),
        ).fetchall()
        return [self._alias_row(row) for row in rows]

    def aliases(self, entity_id: UUID) -> list[AliasRow]:
        rows = self._conn.execute(
            "SELECT * FROM identity_aliases WHERE entity_id = ? ORDER BY seq, surface_form",
            (str(entity_id),),
        ).fetchall()
        return [self._alias_row(row) for row in rows]

    def decisions(self, entity_id: UUID) -> list[DecisionRow]:
        rows = self._conn.execute(
            "SELECT * FROM identity_decisions WHERE entity_id = ? ORDER BY seq",
            (str(entity_id),),
        ).fetchall()
        return [
            DecisionRow(
                seq=row["seq"],
                entity_id=UUID(row["entity_id"]),
                kind=row["kind"],
                detail=row["detail"],
            )
            for row in rows
        ]

    def rekeys_since(self, seq: int) -> list[RekeyRow]:
        rows = self._conn.execute(
            "SELECT * FROM identity_rekeys WHERE seq > ? ORDER BY seq, from_entity, to_entity",
            (seq,),
        ).fetchall()
        return [
            RekeyRow(
                seq=row["seq"],
                kind=row["kind"],
                from_entity=UUID(row["from_entity"]),
                to_entity=UUID(row["to_entity"]),
            )
            for row in rows
        ]

    def reset(self) -> None:
        for table in _TABLES:
            self._conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- fixed table names

    # ---- internals ----------------------------------------------------------------

    @staticmethod
    def _alias_row(row: sqlite3.Row) -> AliasRow:
        return AliasRow(
            entity_id=UUID(row["entity_id"]),
            surface_form=row["surface_form"],
            stance=Stance(row["stance"]),
            seq=row["seq"],
        )

    def _put_entity(self, entity_id: UUID, seq: int, provenance: str) -> None:
        # ON CONFLICT rather than a plain insert: the fold contract allows the last
        # batch to be re-applied after a crash, and re-establishing must be a no-op
        # rather than an error.
        self._conn.execute(
            """
            INSERT INTO identity_entities (entity_id, established_seq, provenance)
            VALUES (?, ?, ?)
            ON CONFLICT (entity_id) DO UPDATE
                SET established_seq = excluded.established_seq,
                    provenance = excluded.provenance
            """,
            (str(entity_id), seq, provenance),
        )

    def _put_alias(self, entity_id: UUID, surface_form: str, stance: Stance, seq: int) -> None:
        self._conn.execute(
            """
            INSERT INTO identity_aliases (entity_id, surface_form, stance, seq)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (entity_id, surface_form) DO UPDATE
                SET stance = excluded.stance, seq = excluded.seq
            """,
            (str(entity_id), normalize_surface_form(surface_form), stance.value, seq),
        )

    def _rekey(self, seq: int, kind: str, from_entity: UUID, to_entity: UUID) -> None:
        self._conn.execute(
            """
            INSERT INTO identity_rekeys (seq, kind, from_entity, to_entity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (seq, from_entity, to_entity) DO NOTHING
            """,
            (seq, kind, str(from_entity), str(to_entity)),
        )

    def _decision(self, seq: int, entity_id: UUID, kind: str, detail: object) -> None:
        self._conn.execute(
            """
            INSERT INTO identity_decisions (seq, entity_id, kind, detail)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (seq, entity_id) DO UPDATE
                SET kind = excluded.kind, detail = excluded.detail
            """,
            (seq, str(entity_id), kind, json.dumps(detail, sort_keys=True)),
        )
