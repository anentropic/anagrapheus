"""The user_version migration runner."""

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from anagrapheus.timeline.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
    SchemaTooNew,
    migrate,
    schema_version,
)
from anagrapheus.timeline.sqlite import SQLiteEventStore


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, autocommit=True)


def _normalized_schema(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    ).fetchall()
    return {" ".join(sql.split()) for (sql,) in rows}


def test_fresh_database_reaches_latest(tmp_path: Path):
    conn = _connect(tmp_path / "fresh.db")
    assert migrate(conn) == LATEST_VERSION
    assert schema_version(conn) == LATEST_VERSION


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
            conn.execute(f"PRAGMA user_version = {version}")
        migrate(conn)
        assert _normalized_schema(conn) == expected, f"divergent schema from v{start}"


def test_newer_file_refuses_to_open(tmp_path: Path):
    path = tmp_path / "future.db"
    conn = _connect(path)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
    conn.close()
    with pytest.raises(SchemaTooNew):
        SQLiteEventStore(path)


def test_store_migrates_on_open(tmp_path: Path):
    path = tmp_path / "auto.db"
    store = SQLiteEventStore(path)
    store.close()
    conn = _connect(path)
    assert schema_version(conn) == LATEST_VERSION
