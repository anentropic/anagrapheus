"""
Infra-schema migrations, tracked per component in a ``schema_versions`` table.

This governs adapter tables only. Event *payload* evolution is handled by
upcast-on-read and never touches the schema. A step is a SQL statement, or a function
of the connection for work SQL cannot do, such as backfilling a column computed in
Python; either runs inside its migration's transaction. Consumers are library users opening a
file, not operators running a CLI, so the runner lives inside connect: each pending
migration applies atomically and bumps its component's row; a fresh database simply
runs the whole list. Downgrade is unsupported — a file from a newer library version
refuses to open.

Versions are per **component** rather than per file because subpackages share one
database: ``timeline`` owns the stream's tables, ``identity`` owns the identity
registry's, and each evolves on its own cadence. ``PRAGMA user_version`` cannot
express that — it is a single integer per file — so it is deliberately unused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from anagrapheus.timeline._rows import EVENT_COLUMNS, decode_event_row
from anagrapheus.timeline.chain import event_hash
from anagrapheus.timeline.errors import TimelineError

if TYPE_CHECKING:
    import sqlite3

__all__ = [
    "COMPONENT",
    "LATEST_VERSION",
    "MIGRATIONS",
    "SchemaTooNew",
    "Step",
    "component_version",
    "migrate",
]


class SchemaTooNew(TimelineError):
    """A component's tables were written by a newer library version."""


COMPONENT: Final = "timeline"

# The version ledger itself: created before any component's list runs, so it cannot
# live inside one. Bootstrapping it is idempotent and carries no version of its own.
_BOOTSTRAP: Final = """
    CREATE TABLE IF NOT EXISTS schema_versions (
        component   TEXT    PRIMARY KEY,
        version     INTEGER NOT NULL,
        updated_at  TEXT    NOT NULL
    ) STRICT
"""

type Step = str | Callable[[sqlite3.Connection], None]
"""One step of a migration: a single SQL statement, or a function run on the connection."""


def _backfill_hashes(conn: sqlite3.Connection) -> None:
    """Chain every event already in the log, in ``seq`` order, as append would have."""
    prev: str | None = None
    rows = conn.execute(f"SELECT {EVENT_COLUMNS} FROM events ORDER BY seq").fetchall()
    for seq, *columns in rows:
        envelope, payload = decode_event_row(*columns)
        prev = event_hash(prev, envelope, payload)
        conn.execute("UPDATE events SET hash = ? WHERE seq = ?", (prev, seq))


# Each migration is a tuple of single steps, executed in one transaction with its
# version bump. `sqlite3.Connection.executescript` is deliberately avoided: its
# transaction semantics would break per-migration atomicity.
MIGRATIONS: Final[tuple[tuple[Step, ...], ...]] = (
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
    # v2 — the hash chain: every event's hash covers its predecessor's. Logs written
    # before it are chained on upgrade, exactly as append would have chained them.
    (
        "ALTER TABLE events ADD COLUMN hash TEXT NOT NULL DEFAULT ''",
        _backfill_hashes,
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)


def component_version(conn: sqlite3.Connection, component: str) -> int:
    """Return a component's current schema version, or 0 if it has never run."""
    conn.execute(_BOOTSTRAP)
    row = conn.execute(
        "SELECT version FROM schema_versions WHERE component = ?", (component,)
    ).fetchone()
    return 0 if row is None else int(row[0])


def migrate(
    conn: sqlite3.Connection,
    component: str = COMPONENT,
    migrations: tuple[tuple[Step, ...], ...] = MIGRATIONS,
) -> int:
    """
    Bring one component's schema to the latest version in its list; return it.

    Requires a connection with ``autocommit=True`` (SQLite-level autocommit): each
    pending migration runs inside an explicit transaction together with its version
    bump, so a crash mid-upgrade leaves a consistent prior version.

    Components share the database and migrate independently — a subpackage passes its
    own name and its own ordered list, and never appears in another's.
    """
    latest = len(migrations)
    current = component_version(conn, component)
    if current > latest:
        raise SchemaTooNew(f"{component} schema is v{current}; this library knows up to v{latest}")
    for version, steps in enumerate(migrations[current:], start=current + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for step in steps:
                if isinstance(step, str):
                    conn.execute(step)
                else:
                    step(conn)
            conn.execute(
                """
                INSERT INTO schema_versions (component, version, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT (component) DO UPDATE
                    SET version = excluded.version, updated_at = excluded.updated_at
                """,
                (component, version),
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return latest
