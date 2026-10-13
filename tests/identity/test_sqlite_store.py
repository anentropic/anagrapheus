"""The identity adapter: its own schema component, sharing the timeline's file."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from anagrapheus.identity import (
    SQLiteIdentityStore,
    alias,
    establish,
    merge,
    register_builtin_events,
    unmerge,
)
from anagrapheus.identity.fold import FOLD_NAME
from anagrapheus.identity.migrations import COMPONENT, LATEST_VERSION, MIGRATIONS
from anagrapheus.timeline import EventTypeRegistry
from anagrapheus.timeline.migrations import COMPONENT as TIMELINE_COMPONENT
from anagrapheus.timeline.migrations import SchemaTooNew, component_version, migrate
from anagrapheus.timeline.sqlite import SQLiteEventStore

from .conftest import build_rig
from .test_fold import snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from anagrapheus.timeline import PendingEvent

    from .conftest import Rig


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    component_version(conn, COMPONENT)
    conn.execute(
        "INSERT INTO schema_versions (component, version, updated_at) VALUES (?, ?, '')"
        " ON CONFLICT (component) DO UPDATE SET version = excluded.version",
        (COMPONENT, version),
    )


def _normalized_schema(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name LIKE 'identity%'"
    ).fetchall()
    return {" ".join(sql.split()) for (sql,) in rows}


def test_identity_versions_independently_of_the_timeline(rig: Rig):
    conn = rig.store.connection
    assert component_version(conn, COMPONENT) == LATEST_VERSION
    assert component_version(conn, TIMELINE_COMPONENT) >= 1


def test_both_components_share_one_file(rig: Rig):
    names = {
        row[0]
        for row in rig.store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"events", "pin_scratch", "identity_entities", "identity_aliases"} <= names


def test_fresh_equals_migrated_from_every_version(tmp_path: Path):
    fresh = sqlite3.connect(tmp_path / "fresh.db", autocommit=True)
    migrate(fresh, COMPONENT, MIGRATIONS)
    expected = _normalized_schema(fresh)

    for start in range(LATEST_VERSION):
        conn = sqlite3.connect(tmp_path / f"from_v{start}.db", autocommit=True)
        for version, statements in enumerate(MIGRATIONS[:start], start=1):
            for statement in statements:
                conn.execute(statement)
            _set_version(conn, version)
        migrate(conn, COMPONENT, MIGRATIONS)
        assert _normalized_schema(conn) == expected, f"divergent schema from v{start}"


def test_a_newer_identity_component_refuses_to_open(tmp_path: Path):
    path = tmp_path / "future.db"
    store = SQLiteEventStore(path)
    conn = store.connection
    _set_version(conn, LATEST_VERSION + 1)
    with pytest.raises(SchemaTooNew, match="identity schema"):
        SQLiteIdentityStore(conn)
    store.close()


def test_reset_empties_the_projection_but_keeps_the_schema(rig: Rig):
    entity_id, event = establish(rig.types, "alice", provenance="signup")
    rig.commit(event, alias(rig.types, entity_id, "Alice", "believed"))
    assert rig.identity_store.entity(entity_id) is not None

    rig.identity_store.reset()

    assert rig.identity_store.entity(entity_id) is None
    assert rig.identity_store.aliases(entity_id) == []
    assert _normalized_schema(rig.store.connection)  # tables still there


def test_split_of_an_unfolded_original_still_records(rig: Rig):
    """A split naming an entity the fold never saw must not lose the new entities."""
    from anagrapheus.identity import split as split_decision

    stranger = UUID(int=99)
    carved = UUID(int=100)
    rig.commit(split_decision(rig.types, stranger, [(carved, ["ghost"])]))

    assert rig.identity_store.entity(carved) is not None
    assert rig.registry.resolve("ghost").sole() == carved


# -- a populated v1 projection --------------------------------------------------------

KEPT, ABSORBED, OTHER = UUID(int=1), UUID(int=2), UUID(int=3)
K, A, B = str(KEPT), str(ABSORBED), str(OTHER)


def _v1_log(types: EventTypeRegistry) -> list[PendingEvent]:
    """
    Two entities merged into a third, one of them sharing a form with the survivor.

    Positions: 1 K "acme", 2 A "acme-old", 3 A "acme-uk" believed, 4 K "acme-uk"
    certain, 5 B "acme-b", 6 A into K, 7 B into K.
    """
    return [
        establish(types, "acme", provenance="crm", entity_id=KEPT)[1],
        establish(types, "acme-old", provenance="import", entity_id=ABSORBED)[1],
        alias(types, ABSORBED, "acme-uk", "believed"),
        alias(types, KEPT, "acme-uk", "certain"),
        establish(types, "acme-b", provenance="ledger", entity_id=OTHER)[1],
        merge(types, kept=KEPT, absorbed=ABSORBED),
        merge(types, kept=KEPT, absorbed=OTHER),
    ]


def _detail(**fields: object) -> str:
    return json.dumps(fields, sort_keys=True)


#: What the v1 fold left after ``_v1_log``: each merge moved the absorbed entity's rows
#: onto the survivor, except "acme-uk", which the survivor already showed, so A's row
#: for it was dropped and recorded as shared.
V1_ROWS: dict[str, list[tuple[object, ...]]] = {
    "identity_entities": [
        (K, 1, "crm", None, None),
        (A, 2, "import", K, 6),
        (B, 5, "ledger", K, 7),
    ],
    "identity_aliases": [
        (K, "acme", "", 1),
        (K, "acme-old", "", 2),
        (K, "acme-uk", "certain", 4),
        (K, "acme-b", "", 5),
    ],
    "identity_decisions": [
        (1, K, "established", _detail(form="acme")),
        (2, A, "established", _detail(form="acme-old")),
        (3, A, "aliased", _detail(form="acme-uk", stance="believed")),
        (4, K, "aliased", _detail(form="acme-uk", stance="certain")),
        (5, B, "established", _detail(form="acme-b")),
        (
            6,
            A,
            "absorbed",
            _detail(
                kept=K,
                absorbed=A,
                moved=[["acme-old", "", 2]],
                shared=[["acme-uk", "believed", 3]],
            ),
        ),
        (6, K, "merged", _detail(kept=K, absorbed=A)),
        (7, B, "absorbed", _detail(kept=K, absorbed=B, moved=[["acme-b", "", 5]], shared=[])),
        (7, K, "merged", _detail(kept=K, absorbed=B)),
    ],
    "identity_rekeys": [(6, "merge", A, K), (7, "merge", B, K)],
}


def _build_v1(path: Path) -> None:
    """The log, and the identity projection at schema v1 as the v1 fold left it."""
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(path)
    try:
        store.append(_v1_log(types))
        conn = store.connection
        for statement in MIGRATIONS[0]:
            conn.execute(statement)
        _set_version(conn, 1)
        for table, rows in V1_ROWS.items():
            marks = ", ".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)  # noqa: S608
        store.commit_position(FOLD_NAME, 7)
    finally:
        store.close()


def _claims(conn: sqlite3.Connection) -> set[tuple[object, ...]]:
    return {
        tuple(row)
        for row in conn.execute(
            "SELECT surface_form, seq, stance, entity_id, path, aside FROM identity_claims"
        )
    }


def _path_members(conn: sqlite3.Connection) -> set[tuple[object, ...]]:
    return {
        tuple(row)
        for row in conn.execute(
            "SELECT surface_form, claim_seq, merge_seq FROM identity_claim_paths"
        )
    }


def test_a_populated_v1_projection_backfills_each_shown_row_as_a_claim(tmp_path: Path):
    """
    Every row v1 shows becomes its holder's own claim; nothing else changes.

    A's "acme-uk" was dropped by the v1 merge and so has no claim, and the rows the
    merges moved onto K stand on K with no path: v1 kept no record of what carried them,
    so no claim is indexed under any merge either.
    """
    _build_v1(tmp_path / "v1.db")

    rig = build_rig(tmp_path / "v1.db")
    try:
        conn = rig.store.connection
        assert component_version(conn, COMPONENT) == 2
        assert _claims(conn) == {
            ("acme", 1, "", K, "[]", 0),
            ("acme-old", 2, "", K, "[]", 0),
            ("acme-uk", 4, "certain", K, "[]", 0),
            ("acme-b", 5, "", K, "[]", 0),
        }
        assert _path_members(conn) == set()
        migrated = snapshot(conn)
        for table, rows in V1_ROWS.items():
            assert sorted(map(repr, migrated[table])) == sorted(map(repr, rows)), table
        assert rig.registry.merged_into(ABSORBED) == KEPT
        assert rig.registry.merged_into(OTHER) == KEPT
        assert rig.store.read_position(FOLD_NAME) == 7
    finally:
        rig.store.close()


def test_the_v1_fixture_is_what_the_fold_writes(tmp_path: Path):
    """
    The hand-written v1 rows are what folding the same log writes today.

    v1 and v2 agree on every table they share for a history with no unmerge, so this
    pins the fixture to the fold rather than to a reading of it.
    """
    rig = build_rig(tmp_path / "fresh.db")
    try:
        rig.commit(*_v1_log(rig.types))
        folded = snapshot(rig.store.connection)
        for table, rows in V1_ROWS.items():
            assert sorted(map(repr, folded[table])) == sorted(map(repr, rows)), table
    finally:
        rig.store.close()


def test_an_unmerge_over_a_migrated_v1_merge_gives_nothing_back_until_rebuild(tmp_path: Path):
    """
    The merge is undone, but the rows it moved have no path to follow back.

    A is canonical again and shows nothing; "acme-old" still resolves to K. Rebuilding
    folds the log afresh, with paths, and A takes back what its merge moved.
    """
    _build_v1(tmp_path / "v1.db")
    rig = build_rig(tmp_path / "v1.db")
    try:
        at = rig.commit(unmerge(rig.types, rig.registry, kept=KEPT, absorbed=ABSORBED))

        assert at == 8
        assert rig.registry.canonical(ABSORBED) == ABSORBED
        assert rig.registry.surface_forms(ABSORBED) == []
        assert rig.registry.resolve("acme-old").sole() == KEPT
        last = rig.registry.history(ABSORBED)[-1]
        assert (last.kind, json.loads(last.detail)["restored"]) == ("unmerged", [])

        rig.rebuild()

        assert _claims(rig.store.connection) == {
            ("acme", 1, "", K, "[]", 0),
            ("acme-old", 2, "", A, "[]", 0),
            ("acme-uk", 3, "believed", A, "[]", 0),
            ("acme-uk", 4, "certain", K, "[]", 0),
            ("acme-b", 5, "", K, "[7]", 0),
        }
        assert _path_members(rig.store.connection) == {("acme-b", 5, 7)}
        shown = {
            entity: {(r.surface_form, r.stance, r.seq) for r in rig.registry.surface_forms(entity)}
            for entity in (KEPT, ABSORBED, OTHER)
        }
        assert shown == {
            KEPT: {("acme", "", 1), ("acme-uk", "certain", 4), ("acme-b", "", 5)},
            ABSORBED: {("acme-old", "", 2), ("acme-uk", "believed", 3)},
            OTHER: set(),
        }
        assert rig.registry.canonical(ABSORBED) == ABSORBED
        assert rig.registry.canonical(OTHER) == KEPT
        assert rig.registry.resolve("acme-old").sole() == ABSORBED
        assert {c.entity_id for c in rig.registry.resolve("acme-uk").candidates} == {
            KEPT,
            ABSORBED,
        }
    finally:
        rig.store.close()
