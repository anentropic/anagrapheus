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
        self._put_alias(event.entity_id, event.first_surface_form, event.stance, seq)
        self._decision(seq, event.entity_id, "established", {"form": event.first_surface_form})

    def aliased(self, event: Aliased, seq: int) -> None:
        self._put_alias(event.entity_id, event.surface_form, event.stance, seq)
        self._decision(
            seq,
            event.entity_id,
            "aliased",
            {"form": event.surface_form, "stance": event.stance},
        )

    def merged(self, event: Merged, seq: int) -> None:
        self._conn.execute(
            "UPDATE identity_entities SET merged_into = ?, merged_seq = ? WHERE entity_id = ?",
            (str(event.kept), seq, str(event.absorbed)),
        )
        detail = {"kept": str(event.kept), "absorbed": str(event.absorbed)}
        # What the merge took off the absorbed entity is what an unmerge gives back, so
        # it is written down here. The first application's record wins: on a
        # re-applied batch the state already reflects later decisions, so what the
        # absorbed entity holds now is not what it held then, and a recount would be
        # wrong both ways — it would find nothing the merge moved and take forms the
        # entity recorded afterwards.
        recorded = self._decision_detail(seq, event.absorbed, "absorbed")
        if recorded is not None and "moved" in recorded:
            self._retake_forms(event, seq, recorded)
        else:
            moved, shared = self._take_forms(event, seq)
            if recorded is None:
                recorded = {**detail, "moved": moved, "shared": shared}
        self._rekey(seq, "merge", event.absorbed, event.kept)
        self._decision(seq, event.absorbed, "absorbed", recorded)
        self._decision(seq, event.kept, "merged", detail)

    def unmerged(self, event: Unmerged, seq: int) -> None:
        detail: dict[str, object] = {"kept": str(event.kept), "absorbed": str(event.absorbed)}
        # On a re-applied batch the state already reflects later decisions, so whether
        # this unmerge matches *now* is not whether it matched the first time. What it
        # did then is in its own decision row, and that row decides.
        prior = self._decision_detail(seq, event.absorbed, "unmerged")
        if self._decision_detail(seq, event.absorbed, "unmerge_ignored") is not None:
            return
        absorbed = self.entity(event.absorbed)
        if absorbed is None or absorbed.merged_into != event.kept:
            if prior is not None:
                # It landed the first time; the merge it withdrew is why nothing
                # matches now.
                return
            current = None if absorbed is None else absorbed.merged_into
            detail["merged_into"] = None if current is None else str(current)
            self._decision(seq, event.absorbed, "unmerge_ignored", detail)
            self._decision(seq, event.kept, "unmerge_ignored", detail)
            return

        merge = (
            None
            if absorbed.merged_seq is None
            else self._decision_detail(absorbed.merged_seq, event.absorbed, "absorbed")
        )
        self._conn.execute(
            "UPDATE identity_entities SET merged_into = NULL, merged_seq = NULL "
            "WHERE entity_id = ?",
            (str(event.absorbed),),
        )
        restored: list[str] = []
        moved = _entries(merge, "moved") if merge is not None else []
        shared = _entries(merge, "shared") if merge is not None else []
        for form, stance, form_seq in moved:
            if self._alias_seq(event.kept, form) is None:
                # A later decision, a split of the survivor, took the form elsewhere.
                # That judgment stands; the unmerge does not reach past it.
                continue
            # Still the row the merge moved means the survivor never recorded the form
            # for itself, so it goes back.
            self._release(event.kept, event.absorbed, form, form_seq)
            restored.append(form)
            self._restore_alias(event.absorbed, form, stance, form_seq)
        for form, stance, form_seq in shared:
            # Both held it before the merge, so both hold it again. The survivor's row
            # may be one it holds only for this merge, kept for it when another merge
            # that had moved the form was withdrawn first.
            self._release(event.kept, event.absorbed, form, form_seq)
            restored.append(form)
            self._restore_alias(event.absorbed, form, stance, form_seq)
        # Split-shaped: a belief keyed on the survivor may have been about either
        # entity, and only what the belief says can tell which.
        self._rekey(seq, "unmerge", event.kept, event.kept)
        self._rekey(seq, "unmerge", event.kept, event.absorbed)
        detail["merged_seq"] = absorbed.merged_seq
        if prior is None:
            prior = {**detail, "restored": sorted(restored)}
        else:
            # Matching again on a re-applied batch means a later merge put the pair
            # back together; that merge re-applies after this. The first record stands.
            detail = {key: prior[key] for key in detail}
        self._decision(seq, event.absorbed, "unmerged", prior)
        self._decision(seq, event.kept, "unmerged", detail)

    def split(self, event: Split, seq: int) -> None:
        origin = self.entity(event.original)
        provenance = origin.provenance if origin is not None else ""
        # Snapshot the source's forms before any of them move. Reading each one inside
        # the loop would make the outcome depend on assignment order, and on a
        # re-applied batch — which the fold contract permits — the rows have already
        # moved, so every stance would silently fall back to ungraded.
        source = {row.surface_form: row.stance for row in self.aliases(event.original)}
        for assignment in event.into:
            self._put_entity(assignment.entity_id, seq, provenance)
            for form in assignment.surface_forms:
                normalized = normalize_surface_form(form)
                stance = source.get(normalized)
                if stance is None:
                    # Nothing to move: either the form was never on the original, or
                    # this batch already ran and the target holds it.
                    stance = self._alias_stance(assignment.entity_id, normalized)
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

    def _take_forms(self, event: Merged, seq: int) -> tuple[list[list[object]], list[list[object]]]:
        """
        Move the absorbed entity's forms to the survivor; return what moved and what both held.

        The absorbed id names nothing once it has yielded, so its surface forms belong
        to the survivor. Leaving them where they were made the projection inconsistent
        with itself — `resolve` canonicalized them onto the survivor while
        `surface_forms` did not list them, and a later split of the survivor could not
        reach them to disambiguate. `identity_decisions` keeps the record of which
        entity originally recorded each form, so nothing is lost.

        Only rows recorded at or before the merge are taken: a form the absorbed entity
        recorded later was never the merge's to move.
        """
        moved: list[list[object]] = []
        shared: list[list[object]] = []
        for row in self.aliases(event.absorbed):
            if row.seq > seq:
                continue
            entry: list[object] = [row.surface_form, row.stance, row.seq]
            if self._alias_row_exists(event.kept, row.surface_form):
                # The survivor's own recording of its own name wins over a duplicate
                # arriving from the absorbed entity. Deterministic without ranking,
                # which this layer cannot do — the ladder belongs to the caller.
                shared.append(entry)
            else:
                moved.append(entry)
                self._put_alias(event.kept, row.surface_form, row.stance, row.seq)
            self._delete_alias(event.absorbed, row.surface_form)
        return moved, shared

    def _retake_forms(self, event: Merged, seq: int, recorded: dict[str, object]) -> None:
        """
        Re-apply a merge from its record: take again exactly the forms it took then.

        A form is taken only while the absorbed entity still holds the row the merge
        saw — a later unmerge gives that row back — and never one recorded after it.
        """
        for form, stance, form_seq in _entries(recorded, "moved"):
            if self._alias_seq(event.absorbed, form) == form_seq:
                self._put_alias(event.kept, form, stance, form_seq)
                self._delete_alias(event.absorbed, form)
        for form, _stance, form_seq in _entries(recorded, "shared"):
            if self._alias_seq(event.absorbed, form) == form_seq:
                self._delete_alias(event.absorbed, form)

    def _release(self, kept: UUID, absorbed: UUID, form: str, form_seq: int) -> None:
        """
        Take a form an unmerge gives back off the survivor, unless it has another claim.

        The survivor's row is the withdrawn merge's when its sequence is the one that
        merge recorded; a row of the survivor's own, or a later decision's, is left be.
        Another merge still standing may have brought the same form, recorded as held
        by both because this one got there first; the survivor keeps the form for that
        one, as it would have had the withdrawn merge never happened.
        """
        if self._alias_seq(kept, form) != form_seq:
            return
        claim = self._standing_claim(kept, absorbed, form)
        if claim is None:
            self._delete_alias(kept, form)
        else:
            self._put_alias(kept, form, claim[0], claim[1])

    def _standing_claim(self, kept: UUID, absorbed: UUID, form: str) -> tuple[str, int] | None:
        """``form``'s stance and sequence from the earliest other standing merge that held it."""
        rows = self._conn.execute(
            "SELECT entity_id, merged_seq FROM identity_entities "
            "WHERE merged_into = ? AND entity_id != ? AND merged_seq IS NOT NULL "
            "ORDER BY merged_seq",
            (str(kept), str(absorbed)),
        ).fetchall()
        for row in rows:
            merge = self._decision_detail(row["merged_seq"], UUID(row["entity_id"]), "absorbed")
            if merge is None:
                continue
            for other, stance, other_seq in _entries(merge, "shared"):
                if other == form:
                    return stance, other_seq
        return None

    def _delete_alias(self, entity_id: UUID, surface_form: str) -> None:
        self._conn.execute(
            "DELETE FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
            (str(entity_id), surface_form),
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

    def _alias_row_exists(self, entity_id: UUID, surface_form: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
            (str(entity_id), surface_form),
        ).fetchone()
        return row is not None

    def _alias_seq(self, entity_id: UUID, surface_form: str) -> int | None:
        row = self._conn.execute(
            "SELECT seq FROM identity_aliases WHERE entity_id = ? AND surface_form = ?",
            (str(entity_id), surface_form),
        ).fetchone()
        return None if row is None else int(row["seq"])

    def _restore_alias(self, entity_id: UUID, surface_form: str, stance: str, seq: int) -> None:
        """Give a form back, unless the entity has recorded it again since."""
        if not self._alias_row_exists(entity_id, surface_form):
            self._put_alias(entity_id, surface_form, stance, seq)

    def _decision_detail(self, seq: int, entity_id: UUID, kind: str) -> dict[str, object] | None:
        """The detail of the decision recorded at ``seq`` for an entity, if it is ``kind``."""
        row = self._conn.execute(
            "SELECT kind, detail FROM identity_decisions WHERE seq = ? AND entity_id = ?",
            (seq, str(entity_id)),
        ).fetchone()
        if row is None or row["kind"] != kind:
            return None
        detail: dict[str, object] = json.loads(row["detail"])
        return detail

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


def _entries(detail: dict[str, object], key: str) -> list[tuple[str, str, int]]:
    """
    The ``[form, stance, seq]`` entries a merge recorded under ``key``.

    A merge folded before merges recorded their forms has none, and an unmerge of it
    restores nothing; rebuilding the projection records them.
    """
    raw = detail.get(key)
    if not isinstance(raw, list):
        return []
    entries: list[tuple[str, str, int]] = []
    for item in cast("list[object]", raw):
        form, stance, seq = cast("list[object]", item)
        entries.append((str(form), str(stance), int(cast("int", seq))))
    return entries
