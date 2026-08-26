"""The identity adapter: its own schema component, sharing the timeline's file."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from anagrapheus.identity import SQLiteIdentityStore, alias, establish
from anagrapheus.identity.migrations import COMPONENT, LATEST_VERSION, MIGRATIONS
from anagrapheus.timeline.migrations import COMPONENT as TIMELINE_COMPONENT
from anagrapheus.timeline.migrations import SchemaTooNew, component_version, migrate
from anagrapheus.timeline.sqlite import SQLiteEventStore

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import Rig


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
            component_version(conn, COMPONENT)
            conn.execute(
                "INSERT INTO schema_versions (component, version, updated_at) VALUES (?, ?, '')"
                " ON CONFLICT (component) DO UPDATE SET version = excluded.version",
                (COMPONENT, version),
            )
        migrate(conn, COMPONENT, MIGRATIONS)
        assert _normalized_schema(conn) == expected, f"divergent schema from v{start}"


def test_a_newer_identity_component_refuses_to_open(tmp_path: Path):
    path = tmp_path / "future.db"
    store = SQLiteEventStore(path)
    conn = store.connection
    component_version(conn, COMPONENT)
    conn.execute(
        "INSERT INTO schema_versions (component, version, updated_at) VALUES (?, ?, '')"
        " ON CONFLICT (component) DO UPDATE SET version = excluded.version",
        (COMPONENT, LATEST_VERSION + 1),
    )
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
