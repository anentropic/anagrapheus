"""The per-component migration runner."""

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from anagrapheus.timeline.migrations import (
    COMPONENT,
    LATEST_VERSION,
    MIGRATIONS,
    SchemaTooNew,
    component_version,
    migrate,
)
from anagrapheus.timeline.sqlite import SQLiteEventStore


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, autocommit=True)


def _normalized_schema(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall()
    return {" ".join(sql.split()) for (sql,) in rows}


def _stamp(conn: sqlite3.Connection, component: str, version: int) -> None:
    """Record a component at a given version, as a historical file would carry it."""
    component_version(conn, component)  # bootstraps schema_versions
    conn.execute(
        "INSERT INTO schema_versions (component, version, updated_at) VALUES (?, ?, '')"
        " ON CONFLICT (component) DO UPDATE SET version = excluded.version",
        (component, version),
    )


def test_fresh_database_reaches_latest(tmp_path: Path):
    conn = _connect(tmp_path / "fresh.db")
    assert migrate(conn) == LATEST_VERSION
    assert component_version(conn, COMPONENT) == LATEST_VERSION


def test_unrun_component_reports_zero(tmp_path: Path):
    conn = _connect(tmp_path / "empty.db")
    assert component_version(conn, "never-run") == 0


def test_migrate_is_idempotent(tmp_path: Path):
    conn = _connect(tmp_path / "again.db")
    migrate(conn)
    assert migrate(conn) == LATEST_VERSION


def test_fresh_equals_migrated_from_every_version(tmp_path: Path):
    """A file created at any historical version upgrades to the same schema as fresh."""
    fresh = _connect(tmp_path / "fresh.db")
    migrate(fresh)
    expected = _normalized_schema(fresh)

    for start in range(LATEST_VERSION):
        conn = _connect(tmp_path / f"from_v{start}.db")
        for version, statements in enumerate(MIGRATIONS[:start], start=1):
            for statement in statements:
                conn.execute(statement)
            _stamp(conn, COMPONENT, version)
        migrate(conn)
        assert _normalized_schema(conn) == expected, f"divergent schema from v{start}"


def test_newer_file_refuses_to_open(tmp_path: Path):
    path = tmp_path / "future.db"
    conn = _connect(path)
    _stamp(conn, COMPONENT, LATEST_VERSION + 1)
    conn.close()
    with pytest.raises(SchemaTooNew):
        SQLiteEventStore(path)


def test_components_migrate_independently(tmp_path: Path):
    """One component's version never advances another's."""
    conn = _connect(tmp_path / "two.db")
    other = (("CREATE TABLE other_thing (id INTEGER PRIMARY KEY) STRICT",),)
    migrate(conn)
    assert migrate(conn, "other", other) == 1
    assert component_version(conn, COMPONENT) == LATEST_VERSION
    assert component_version(conn, "other") == 1


def test_store_migrates_on_open(tmp_path: Path):
    path = tmp_path / "auto.db"
    store = SQLiteEventStore(path)
    store.close()
    conn = _connect(path)
    assert component_version(conn, COMPONENT) == LATEST_VERSION


def test_store_exposes_its_connection(tmp_path: Path):
    """Other components share the file through this connection, not a second one."""
    store = SQLiteEventStore(tmp_path / "shared.db")
    try:
        assert component_version(store.connection, COMPONENT) == LATEST_VERSION
    finally:
        store.close()
