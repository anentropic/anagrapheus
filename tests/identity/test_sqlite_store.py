"""The identity adapter: its own schema component, sharing the timeline's file."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from anagrapheus.identity import (
    Merged,
    OutOfOrderDecision,
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


def test_a_decision_older_than_the_latest_applied_is_refused(rig: Rig):
    """
    Decisions land in log order or not at all; the latest one may land again.

    A merge folded behind a later decision would rank what it carried as newer than
    what came after it. Re-applying the latest decision, as a batch re-applied after a
    crash does, changes nothing and does not raise.
    """
    kept, first = establish(rig.types, "acme", provenance="crm")
    absorbed, second = establish(rig.types, "acme-old", provenance="import")
    other, third = establish(rig.types, "acme-b", provenance="ledger")
    last = rig.commit(first, second, third)
    # Written to the store directly, so the positions can be chosen: the merge at
    # ``last + 2`` lands, and one at ``last + 1`` would land behind it.
    rig.identity_store.merged(Merged(kept=kept, absorbed=absorbed), last + 2)
    landed = snapshot(rig.store.connection)

    with pytest.raises(OutOfOrderDecision, match="log order"):
        rig.identity_store.merged(Merged(kept=kept, absorbed=other), last + 1)
    rig.identity_store.merged(Merged(kept=kept, absorbed=absorbed), last + 2)

    assert snapshot(rig.store.connection) == landed
    row = rig.identity_store.entity(other)
    assert row is not None
    assert row.merged_into is None


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
            "SELECT surface_form, seq, stance, entity_id, aside FROM identity_claims"
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
        assert component_version(conn, COMPONENT) == LATEST_VERSION
        assert _claims(conn) == {
            ("acme", 1, "", K, 0),
            ("acme-old", 2, "", K, 0),
            ("acme-uk", 4, "certain", K, 0),
            ("acme-b", 5, "", K, 0),
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
            ("acme", 1, "", K, 0),
            ("acme-old", 2, "", A, 0),
            ("acme-uk", 3, "believed", A, 0),
            ("acme-uk", 4, "certain", K, 0),
            ("acme-b", 5, "", K, 0),
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


# -- a populated v2 projection --------------------------------------------------------


def _v2_log(types: EventTypeRegistry) -> list[PendingEvent]:
    """
    A merge chained onto another, so two claims carry a path of two merges.

    Positions: 1 K "acme", 2 A "acme-old", 3 A "acme-uk" believed, 4 K "acme-uk"
    certain, 5 B "acme-b", 6 A into B, 7 B into K.
    """
    return [
        establish(types, "acme", provenance="crm", entity_id=KEPT)[1],
        establish(types, "acme-old", provenance="import", entity_id=ABSORBED)[1],
        alias(types, ABSORBED, "acme-uk", "believed"),
        alias(types, KEPT, "acme-uk", "certain"),
        establish(types, "acme-b", provenance="ledger", entity_id=OTHER)[1],
        merge(types, kept=OTHER, absorbed=ABSORBED),
        merge(types, kept=KEPT, absorbed=OTHER),
    ]


#: What the v2 fold left after ``_v2_log``: everything stands on K, and each claim the
#: merges carried keeps its path twice, as JSON latest first and as membership rows.
V2_ROWS: dict[str, list[tuple[object, ...]]] = {
    "identity_entities": [
        (K, 1, "crm", None, None),
        (A, 2, "import", B, 6),
        (B, 5, "ledger", K, 7),
    ],
    "identity_aliases": [
        (K, "acme", "", 1),
        (K, "acme-old", "", 2),
        (K, "acme-uk", "certain", 4),
        (K, "acme-b", "", 5),
    ],
    "identity_claims": [
        ("acme", 1, "", K, "[]", 0),
        ("acme-old", 2, "", K, "[7, 6]", 0),
        ("acme-uk", 3, "believed", K, "[7, 6]", 0),
        ("acme-uk", 4, "certain", K, "[]", 0),
        ("acme-b", 5, "", K, "[7]", 0),
    ],
    "identity_claim_paths": [
        ("acme-old", 2, 6),
        ("acme-old", 2, 7),
        ("acme-uk", 3, 6),
        ("acme-uk", 3, 7),
        ("acme-b", 5, 7),
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
                kept=B,
                absorbed=A,
                moved=[["acme-old", "", 2], ["acme-uk", "believed", 3]],
                shared=[],
            ),
        ),
        (6, B, "merged", _detail(kept=B, absorbed=A)),
        (
            7,
            B,
            "absorbed",
            _detail(
                kept=K,
                absorbed=B,
                moved=[["acme-old", "", 2], ["acme-b", "", 5]],
                shared=[["acme-uk", "believed", 3]],
            ),
        ),
        (7, K, "merged", _detail(kept=K, absorbed=B)),
    ],
    "identity_rekeys": [(6, "merge", A, B), (7, "merge", B, K)],
}


def _build_v2(path: Path, *, with_path_rows: bool) -> None:
    """
    The log, and the identity projection at schema v2 as the v2 fold left it.

    Without ``with_path_rows`` the membership rows are left out, so only the JSON
    paths say which merges carried what.
    """
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(path)
    try:
        store.append(_v2_log(types))
        conn = store.connection
        for statements in MIGRATIONS[:2]:
            for statement in statements:
                conn.execute(statement)
        _set_version(conn, 2)
        for table, rows in V2_ROWS.items():
            if table == "identity_claim_paths" and not with_path_rows:
                continue
            marks = ", ".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)  # noqa: S608
        store.commit_position(FOLD_NAME, 7)
    finally:
        store.close()


def _without_json_paths(rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """v2 claim rows as v3 keeps them: the same columns but the JSON path."""
    return [(form, seq, stance, entity, aside) for form, seq, stance, entity, _, aside in rows]


def test_the_v2_fixture_is_what_the_fold_writes(tmp_path: Path):
    """
    The hand-written v2 rows are what folding the same log writes today.

    Each JSON path is the claim's membership rows read latest merge first, so the
    fixture's paths are pinned to the fold as well.
    """
    rig = build_rig(tmp_path / "fresh.db")
    try:
        rig.commit(*_v2_log(rig.types))
        conn = rig.store.connection
        folded = snapshot(conn)
        for table, rows in V2_ROWS.items():
            if table == "identity_claims":
                rows = _without_json_paths(rows)
            assert sorted(map(repr, folded[table])) == sorted(map(repr, rows)), table
        for form, seq, _, _, path, _ in V2_ROWS["identity_claims"]:
            steps = conn.execute(
                "SELECT merge_seq FROM identity_claim_paths "
                "WHERE surface_form = ? AND claim_seq = ? ORDER BY merge_seq DESC",
                (form, seq),
            ).fetchall()
            assert [step for (step,) in steps] == json.loads(str(path)), (form, seq)
    finally:
        rig.store.close()


@pytest.mark.parametrize("with_path_rows", [True, False], ids=["v2-rows", "json-only"])
def test_a_populated_v2_projection_keeps_its_paths_through_v3(
    tmp_path: Path, *, with_path_rows: bool
):
    """
    Migrating drops the JSON column and keeps every path as membership rows.

    Rows already there are kept and missing ones are filled in from the JSON. The
    unmerges after migrating then send back exactly what each merge carried, and the
    result is what rebuilding the log from scratch gives.
    """
    _build_v2(tmp_path / "v2.db", with_path_rows=with_path_rows)
    rig = build_rig(tmp_path / "v2.db")
    try:
        conn = rig.store.connection
        assert component_version(conn, COMPONENT) == LATEST_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_claims)")}
        assert "path" not in columns
        migrated = snapshot(conn)
        for table, rows in V2_ROWS.items():
            if table == "identity_claims":
                rows = _without_json_paths(rows)
            assert sorted(map(repr, migrated[table])) == sorted(map(repr, rows)), table

        rig.commit(unmerge(rig.types, rig.registry, kept=OTHER, absorbed=ABSORBED))

        last = rig.registry.history(ABSORBED)[-1]
        assert (last.kind, json.loads(last.detail)["restored"]) == (
            "unmerged",
            ["acme-old", "acme-uk"],
        )
        assert _path_members(conn) == {("acme-b", 5, 7)}
        shown = {
            entity: {(r.surface_form, r.stance, r.seq) for r in rig.registry.surface_forms(entity)}
            for entity in (KEPT, ABSORBED, OTHER)
        }
        assert shown == {
            KEPT: {("acme", "", 1), ("acme-uk", "certain", 4), ("acme-b", "", 5)},
            ABSORBED: {("acme-old", "", 2), ("acme-uk", "believed", 3)},
            OTHER: set(),
        }

        rig.commit(unmerge(rig.types, rig.registry, kept=KEPT, absorbed=OTHER))

        assert _path_members(conn) == set()
        assert [r.surface_form for r in rig.registry.surface_forms(OTHER)] == ["acme-b"]
        assert rig.registry.resolve("acme-b").sole() == OTHER
        assert rig.registry.resolve("acme-old").sole() == ABSORBED

        before = snapshot(conn)
        rig.rebuild()
        assert snapshot(conn) == before
    finally:
        rig.store.close()
