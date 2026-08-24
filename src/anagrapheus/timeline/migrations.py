"""
Infra-schema migrations, tracked in ``PRAGMA user_version``.

This governs the adapter's own tables only. Event *payload* evolution is handled by
upcast-on-read and never touches the schema. Consumers are library users opening a
file, not operators running a CLI, so the runner lives inside connect: each pending
migration applies atomically and bumps ``user_version``; a fresh database simply runs
the whole list. Downgrade is unsupported — a file from a newer library version
refuses to open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from anagrapheus.timeline.errors import TimelineError

if TYPE_CHECKING:
    import sqlite3

__all__ = ["LATEST_VERSION", "MIGRATIONS", "SchemaTooNew", "migrate", "schema_version"]


class SchemaTooNew(TimelineError):
    """The database was written by a newer library version."""


# Each migration is a tuple of single statements, executed in one transaction with its
# user_version bump. `sqlite3.Connection.executescript` is deliberately avoided: its
# transaction semantics would break per-migration atomicity.
MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    # v1 — initial schema: events, positions, pin_scratch.
    (
        """
        CREATE TABLE events (
            seq             INTEGER PRIMARY KEY,
            event_id        TEXT    NOT NULL UNIQUE,
            namespace       TEXT    NOT NULL,
            type            TEXT    NOT NULL,
            schema_version  INTEGER NOT NULL,
            causation_id    TEXT,
            correlation_id  TEXT,
            created_at      TEXT    NOT NULL,
            payload         TEXT    NOT NULL
        ) STRICT
        """,
        "CREATE INDEX ix_events_correlation ON events (correlation_id)",
        """
        CREATE TABLE projection_positions (
            projection  TEXT    PRIMARY KEY,
            seq         INTEGER NOT NULL,
            updated_at  TEXT    NOT NULL
        ) STRICT
        """,
        """
        CREATE TABLE pin_scratch (
            unit_id     TEXT    NOT NULL,
            pin_key     TEXT    NOT NULL,
            kind        TEXT    NOT NULL,
            result      TEXT    NOT NULL,
            attempts    INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            PRIMARY KEY (unit_id, pin_key)
        ) STRICT
        """,
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)


def schema_version(conn: sqlite3.Connection) -> int:
    """Return the file's current schema version."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def migrate(conn: sqlite3.Connection) -> int:
    """
    Bring a connection's schema to the latest version; return it.

    Requires a connection with ``autocommit=True`` (SQLite-level autocommit): each
    pending migration runs inside an explicit transaction together with its
    ``user_version`` bump, so a crash mid-upgrade leaves a consistent prior version.
    """
    current = schema_version(conn)
    if current > LATEST_VERSION:
        raise SchemaTooNew(
            f"database schema is v{current}; this library knows up to v{LATEST_VERSION}"
        )
    for version, statements in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return LATEST_VERSION
