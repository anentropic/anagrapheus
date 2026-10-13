"""
The identity adapter: the only module in this subpackage containing SQL.

It shares the timeline's connection and therefore its file and its transactions —
which is what lets the projection's state and its position commit together, so a
crash can never leave the registry ahead of or behind the position that guards it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, cast
from uuid import UUID

from anagrapheus.identity.events import normalize_surface_form
from anagrapheus.identity.ladder import UNGRADED
from anagrapheus.identity.migrations import COMPONENT, MIGRATIONS
from anagrapheus.identity.store import AliasRow, DecisionRow, EntityRow, RekeyRow
from anagrapheus.timeline.migrations import migrate

if TYPE_CHECKING:
    from anagrapheus.identity.events import Aliased, Established, Merged, Split, Unmerged
    from anagrapheus.identity.ladder import Ladder

__all__ = ["SQLiteIdentityStore"]

_TABLES = (
    "identity_rekeys",
    "identity_decisions",
    "identity_claims",
    "identity_aliases",
    "identity_entities",
)


class SQLiteIdentityStore:
    """
    Implements ``IdentityStore`` over the same file the timeline uses.

    Every recording of a surface form is kept as a row of its own, a *claim*, named by
    the form and the position that recorded it, and stands on one entity at a time.
    Each claim also keeps its *path*: the merges that carried it to where it stands,
    latest first. A merge moves every claim standing on the absorbed entity to the
    survivor and adds itself to their paths; an unmerge sends every claim whose path
    runs through its merge back to the absorbed entity, wherever later merges of the
    survivor carried it, and drops that merge and every later one from the path. That
    is the whole rule: what an unmerge withdraws is found by the specific rows its
    merge moved, never by a form's name.

    An entity shows one row per form, the first of the claims standing on it: its own
    recordings first, newest first, then what merges brought, the earliest merge first
    and each merge's claims in the order they stood on the entity they came from. That
    is the order the claims would stand in had no withdrawn merge ever happened.

    A split takes the row the original shows to the new entity, as a recording of the
    new entity's own; whatever else stood behind that row on the original is set aside
    there, shown by no one, until an unmerge of a merge that carried it sends it back.

    Each decision is applied once. On a re-applied batch the state already reflects
    every decision in it, so a decision that finds its own record at its position has
    nothing left to do.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, COMPONENT, MIGRATIONS)

    # ---- writes: one per identity role -------------------------------------------

    def established(self, event: Established, seq: int) -> None:
        if self._applied(seq):
            return
        self._put_entity(event.entity_id, seq, event.provenance)
        self._record(event.entity_id, event.first_surface_form, event.stance, seq)
        self._decision(seq, event.entity_id, "established", {"form": event.first_surface_form})

    def aliased(self, event: Aliased, seq: int) -> None:
        if self._applied(seq):
            return
        self._record(event.entity_id, event.surface_form, event.stance, seq)
        self._decision(
            seq,
            event.entity_id,
            "aliased",
            {"form": event.surface_form, "stance": event.stance},
        )

    def merged(self, event: Merged, seq: int) -> None:
        if self._applied(seq):
            return
        detail = {"kept": str(event.kept), "absorbed": str(event.absorbed)}
        absorbed = self.entity(event.absorbed)
        if absorbed is not None and absorbed.merged_into is not None:
            # An entity yields to one survivor at a time. Re-pointing an absorbed
            # entity would leave what its standing merge moved on the first survivor,
            # out of reach of any unmerge, so a merge of one already merged changes
            # nothing.
            ignored = {**detail, "merged_into": str(absorbed.merged_into)}
            self._decision(seq, event.absorbed, "merge_ignored", ignored)
            self._decision(seq, event.kept, "merge_ignored", ignored)
            return
        self._conn.execute(
            "UPDATE identity_entities SET merged_into = ?, merged_seq = ? WHERE entity_id = ?",
            (str(event.kept), seq, str(event.absorbed)),
        )
        # For the audit trail: which of the rows the absorbed entity showed the
        # survivor now shows, and which the survivor already had a row of its own for.
        moved: list[list[object]] = []
        shared: list[list[object]] = []
        for row in self.aliases(event.absorbed):
            entry: list[object] = [row.surface_form, row.stance, row.seq]
            held = self._shown(event.kept, row.surface_form) is not None
            (shared if held else moved).append(entry)
        self._carry(event.absorbed, event.kept, seq)
        self._rekey(seq, "merge", event.absorbed, event.kept)
        self._decision(
            seq, event.absorbed, "absorbed", {**detail, "moved": moved, "shared": shared}
        )
        self._decision(seq, event.kept, "merged", detail)

    def unmerged(self, event: Unmerged, seq: int) -> None:
        if self._applied(seq):
            return
        detail: dict[str, object] = {"kept": str(event.kept), "absorbed": str(event.absorbed)}
        absorbed = self.entity(event.absorbed)
        if absorbed is None or absorbed.merged_into != event.kept:
            current = None if absorbed is None else absorbed.merged_into
            detail["merged_into"] = None if current is None else str(current)
            self._decision(seq, event.absorbed, "unmerge_ignored", detail)
            self._decision(seq, event.kept, "unmerge_ignored", detail)
            return
        self._conn.execute(
            "UPDATE identity_entities SET merged_into = NULL, merged_seq = NULL "
            "WHERE entity_id = ?",
            (str(event.absorbed),),
        )
        merged_seq = absorbed.merged_seq
        restored = [] if merged_seq is None else self._withdraw(merged_seq, event.absorbed)
        # Split-shaped: a belief keyed on the survivor may have been about either
        # entity, and only what the belief says can tell which.
        self._rekey(seq, "unmerge", event.kept, event.kept)
        self._rekey(seq, "unmerge", event.kept, event.absorbed)
        detail["merged_seq"] = merged_seq
        self._decision(seq, event.absorbed, "unmerged", {**detail, "restored": restored})
        self._decision(seq, event.kept, "unmerged", detail)

    def split(self, event: Split, seq: int) -> None:
        if self._applied(seq):
            return
        origin = self.entity(event.original)
        provenance = origin.provenance if origin is not None else ""
        for assignment in event.into:
            self._put_entity(assignment.entity_id, seq, provenance)
            for form in assignment.surface_forms:
                normalized = normalize_surface_form(form)
                shown = self._shown(event.original, normalized)
                if shown is None:
                    # Nothing to move: the original shows no such form.
                    stance = self._alias_stance(assignment.entity_id, normalized)
                else:
                    stance = shown[1]
                    self._conn.execute(
                        "DELETE FROM identity_claims WHERE surface_form = ? AND seq = ?",
                        (normalized, shown[0]),
                    )
                # The split judged the form to be the new entity's, so nothing else
                # standing behind it on the original shows there again.
                self._conn.execute(
                    "UPDATE identity_claims SET aside = 1 "
                    "WHERE entity_id = ? AND surface_form = ? AND aside = 0",
                    (str(event.original), normalized),
                )
                self._show(event.original, normalized)
                self._record(assignment.entity_id, normalized, stance, seq)
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

    # ---- claims -------------------------------------------------------------------

    def _record(self, entity_id: UUID, surface_form: str, stance: str, seq: int) -> None:
        """A new recording of a form on an entity: a claim of its own, shown first."""
        normalized = normalize_surface_form(surface_form)
        self._conn.execute(
            "INSERT INTO identity_claims (surface_form, seq, stance, entity_id, path, aside) "
            "VALUES (?, ?, ?, ?, '[]', 0)",
            (normalized, seq, stance, str(entity_id)),
        )
        self._show(entity_id, normalized)

    def _carry(self, absorbed: UUID, kept: UUID, seq: int) -> None:
        """
        Move every claim standing on the absorbed entity to the survivor.

        The absorbed id names nothing once it has yielded, so resolving or splitting
        the survivor has to reach its forms. A claim set aside by a split stays where
        the split left it.
        """
        rows = self._conn.execute(
            "SELECT surface_form, seq, path FROM identity_claims WHERE entity_id = ? AND aside = 0",
            (str(absorbed),),
        ).fetchall()
        for row in rows:
            path = [seq, *_path(row["path"])]
            self._conn.execute(
                "UPDATE identity_claims SET entity_id = ?, path = ? "
                "WHERE surface_form = ? AND seq = ?",
                (str(kept), json.dumps(path), row["surface_form"], row["seq"]),
            )
        for form in sorted({str(row["surface_form"]) for row in rows}):
            self._show(absorbed, form)
            self._show(kept, form)

    def _withdraw(self, merged_seq: int, absorbed: UUID) -> list[str]:
        """
        Send back every claim the merge at ``merged_seq`` carried; return their forms.

        Each one is found by its path, wherever later merges took it, and taken off the
        path together with every later merge. A claim a split has taken since is gone,
        and that judgment stands. One set aside behind it goes back like any other: the
        split judged the row it took, not the merge that brought this one.
        """
        rows = self._conn.execute(
            "SELECT surface_form, seq, entity_id, path FROM identity_claims "
            "WHERE EXISTS (SELECT 1 FROM json_each(identity_claims.path) WHERE value = ?)",
            (merged_seq,),
        ).fetchall()
        touched: set[tuple[str, str]] = set()
        for row in rows:
            path = _path(row["path"])
            rest = path[path.index(merged_seq) + 1 :]
            self._conn.execute(
                "UPDATE identity_claims SET entity_id = ?, path = ?, aside = 0 "
                "WHERE surface_form = ? AND seq = ?",
                (str(absorbed), json.dumps(rest), row["surface_form"], row["seq"]),
            )
            touched.add((str(row["entity_id"]), str(row["surface_form"])))
            touched.add((str(absorbed), str(row["surface_form"])))
        for entity_id, form in sorted(touched):
            self._show(UUID(entity_id), form)
        return sorted({str(row["surface_form"]) for row in rows})

    def _shown(self, entity_id: UUID, surface_form: str) -> tuple[int, str] | None:
        """The sequence and stance of the claim an entity shows for a form, if any."""
        rows = self._conn.execute(
            "SELECT seq, stance, path FROM identity_claims "
            "WHERE entity_id = ? AND surface_form = ? AND aside = 0",
            (str(entity_id), normalize_surface_form(surface_form)),
        ).fetchall()
        if not rows:
            return None
        first = min(rows, key=lambda row: _rank(int(row["seq"]), _path(row["path"])))
        return int(first["seq"]), str(first["stance"])

    def _show(self, entity_id: UUID, surface_form: str) -> None:
        """Bring the row an entity shows for a form into line with its claims."""
        shown = self._shown(entity_id, surface_form)
        if shown is None:
            self._conn.execute(
                "DELETE FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
                (str(entity_id), surface_form),
            )
        else:
            self._put_alias(entity_id, surface_form, shown[1], shown[0])

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

    def candidates(self, surface_form: str, *, ladder: Ladder | None = None) -> list[AliasRow]:
        params: list[object] = [normalize_surface_form(surface_form)]
        order = "seq"
        if ladder is not None and ladder.rungs:
            # Rung tokens are application data, so they are bound as parameters; only
            # the rank literals are generated here. An unlisted token falls to -1 and
            # sorts last, which is what Ladder.rank promises.
            whens = " ".join(f"WHEN ? THEN {rank}" for rank in range(len(ladder.rungs)))
            order = f"CASE stance {whens} ELSE -1 END DESC, seq"
            params.extend(ladder.rungs)
        rows = self._conn.execute(
            f"SELECT * FROM identity_aliases WHERE surface_form = ? ORDER BY {order}",  # noqa: S608
            params,
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
            stance=row["stance"],
            seq=row["seq"],
        )

    def _applied(self, seq: int) -> bool:
        """Whether the decision at ``seq`` has already been folded."""
        row = self._conn.execute(
            "SELECT 1 FROM identity_decisions WHERE seq = ? LIMIT 1", (seq,)
        ).fetchone()
        return row is not None

    def _alias_stance(self, entity_id: UUID, surface_form: str) -> str:
        """
        The stance already recorded for a form on an entity, else ungraded.

        Ungraded rather than an invented grading: this layer has no vocabulary of its
        own to guess in.
        """
        row = self._conn.execute(
            "SELECT stance FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
            (str(entity_id), surface_form),
        ).fetchone()
        return UNGRADED if row is None else str(row["stance"])

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

    def _put_alias(self, entity_id: UUID, surface_form: str, stance: str, seq: int) -> None:
        self._conn.execute(
            """
            INSERT INTO identity_aliases (entity_id, surface_form, stance, seq)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (entity_id, surface_form) DO UPDATE
                SET stance = excluded.stance, seq = excluded.seq
            """,
            (str(entity_id), normalize_surface_form(surface_form), stance, seq),
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


def _path(raw: object) -> list[int]:
    """A claim's path: the positions of the merges that carried it, latest first."""
    return [int(cast("int", seq)) for seq in cast("list[object]", json.loads(str(raw)))]


def _rank(seq: int, path: list[int]) -> tuple[tuple[int, int], ...]:
    """
    Where a claim stands among the claims of one form on one entity: lowest shows.

    Read from the entity it stands on down to the one that recorded it, each step is
    either the merge that brought it, ranked by when, or the recording itself. A
    recording outranks anything a merge brought, and the newest recording outranks
    older ones.
    """
    return (*((1, merged) for merged in path), (0, -seq))
