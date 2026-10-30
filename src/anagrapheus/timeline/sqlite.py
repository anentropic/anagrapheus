"""
The SQLite adapter: the only module in the package containing SQL.

One database file holds all three tables. The pin scratch shares the events file
because promote and abandon must move rows scratch-to-events atomically — a single
transaction in one file. "Scratch is never canon" is structural, not physical:
:meth:`SQLiteEventStore.read` only ever queries ``events``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from anagrapheus.timeline.chain import event_hash
from anagrapheus.timeline.envelope import Envelope, StoredEvent
from anagrapheus.timeline.errors import ConcurrencyError
from anagrapheus.timeline.migrations import COMPONENT, MIGRATIONS, migrate
from anagrapheus.timeline.store import ScratchRow

if TYPE_CHECKING:
    import os
    from collections.abc import Generator, Iterator, Sequence

    from pydantic import JsonValue

    from anagrapheus.timeline.envelope import PendingEvent

__all__ = ["SQLiteEventStore"]

_log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class SQLiteEventStore:
    """
    Implements ``EventStore`` and ``PinScratchStore`` over a single SQLite file.

    The connection runs in SQLite-level autocommit; every multi-statement operation
    takes an explicit transaction via :meth:`transaction`. Pending migrations apply on
    connect, so a consumer's file upgrades transparently on open.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._conn = sqlite3.connect(path, autocommit=True)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        migrate(self._conn, COMPONENT, MIGRATIONS)
        self._txn_depth = 0

    @property
    def connection(self) -> sqlite3.Connection:
        """
        The underlying connection, for other components sharing this database.

        Exposed so a projection whose state lives in this same file — the identity
        registry, for one — migrates its own tables and writes through the same
        connection, which is what lets its state and its position commit together
        inside :meth:`transaction`.
        """
        return self._conn

    @property
    def in_transaction(self) -> bool:
        """Whether a transaction is open on the connection, by :meth:`transaction` or not."""
        return self._conn.in_transaction

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """
        One explicit transaction; nested entry becomes a savepoint.

        A nested scope that fails rolls back only its own writes, so an operation
        like ``append`` keeps its atomicity guarantee even when a caller catches its
        failure inside an outer transaction. Folds that keep their state in this same
        file receive this connection as their ``txn``, so projection writes and the
        position commit move together.
        """
        depth = self._txn_depth
        savepoint = f"anagrapheus_sp_{depth}" if depth else None
        if savepoint is None:
            self._conn.execute("BEGIN IMMEDIATE")
        else:
            self._conn.execute(f"SAVEPOINT {savepoint}")
        self._txn_depth = depth + 1
        try:
            yield self._conn
        except BaseException:
            if savepoint is None:
                self._conn.execute("ROLLBACK")
            else:
                self._conn.execute(f"ROLLBACK TO {savepoint}")
                self._conn.execute(f"RELEASE {savepoint}")
            raise
        finally:
            self._txn_depth = depth
        if savepoint is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute(f"RELEASE {savepoint}")

    # -- EventStore ------------------------------------------------------------

    def append(self, batch: Sequence[PendingEvent], *, expected_last_seq: int | None = None) -> int:
        """Append atomically, persist-first; see the port docstring."""
        with self.transaction() as conn:
            last = self._last_seq(conn)
            if expected_last_seq is not None and expected_last_seq != last:
                raise ConcurrencyError(
                    f"expected last_seq {expected_last_seq}, found {last}: "
                    "a second writer is a broken composition, not contention"
                )
            prev = self._head_hash(conn)
            for offset, pending in enumerate(batch, start=1):
                env = pending.envelope
                prev = event_hash(prev, env, pending.payload)
                conn.execute(
                    "INSERT INTO events (seq, event_id, namespace, type, schema_version,"
                    " causation_id, correlation_id, created_at, payload, hash)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        last + offset,
                        str(env.event_id),
                        env.namespace,
                        env.type,
                        env.schema_version,
                        str(env.causation_id) if env.causation_id else None,
                        str(env.correlation_id) if env.correlation_id else None,
                        env.created_at.isoformat(),
                        json.dumps(pending.payload, ensure_ascii=False),
                        prev,
                    ),
                )
        # After the `with`, so a batch whose own write failed is never reported. One
        # appended inside a caller's transaction can still roll back with it later.
        if batch and _log.isEnabledFor(logging.DEBUG):
            _log.debug(
                "appended seq %d..%d: %s",
                last + 1,
                last + len(batch),
                ", ".join(p.envelope.type for p in batch),
            )
        return last + len(batch)

    def read(self, since_seq: int = 0, limit: int | None = None) -> Iterator[StoredEvent]:
        """Read events with ``seq > since_seq`` in order; content never filters here."""
        query = "SELECT * FROM events WHERE seq > ? ORDER BY seq"
        params: tuple[int, ...] = (since_seq,)
        if limit is not None:
            query += " LIMIT ?"
            params = (since_seq, limit)
        for row in self._conn.execute(query, params):
            yield _stored_event(row)

    def last_seq(self) -> int:
        """Return the highest assigned ``seq``, or 0 for an empty stream."""
        return self._last_seq(self._conn)

    @staticmethod
    def _last_seq(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return int(row[0])

    @staticmethod
    def _head_hash(conn: sqlite3.Connection) -> str | None:
        row = conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return str(row[0]) if row is not None else None

    def read_position(self, projection: str) -> int:
        """Return the last folded ``seq`` for a projection, or 0."""
        row = self._conn.execute(
            "SELECT seq FROM projection_positions WHERE projection = ?", (projection,)
        ).fetchone()
        return int(row["seq"]) if row is not None else 0

    def commit_position(self, projection: str, seq: int) -> None:
        """Record a projection's progress; joins an open transaction if one exists."""
        self._conn.execute(
            "INSERT INTO projection_positions (projection, seq, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (projection) DO UPDATE SET seq = excluded.seq,"
            " updated_at = excluded.updated_at",
            (projection, seq, _now_iso()),
        )

    # -- PinScratchStore -------------------------------------------------------

    def scratch_get(self, unit_id: UUID, pin_key: str) -> ScratchRow | None:
        """Return the pinned row for a key within a unit of work, if any."""
        row = self._conn.execute(
            "SELECT * FROM pin_scratch WHERE unit_id = ? AND pin_key = ?",
            (str(unit_id), pin_key),
        ).fetchone()
        return _scratch_row(row) if row is not None else None

    def scratch_put(self, row: ScratchRow) -> None:
        """Persist a fresh pin."""
        self._conn.execute(
            "INSERT INTO pin_scratch (unit_id, pin_key, kind, result, attempts, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(row.unit_id),
                row.pin_key,
                row.kind,
                json.dumps(row.result, ensure_ascii=False),
                row.attempts,
                _now_iso(),
            ),
        )

    def scratch_bump(self, unit_id: UUID, pin_key: str) -> int:
        """Increment and return the attempt count for an existing pin."""
        cursor = self._conn.execute(
            "UPDATE pin_scratch SET attempts = attempts + 1"
            " WHERE unit_id = ? AND pin_key = ? RETURNING attempts",
            (str(unit_id), pin_key),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"no scratch pin for {pin_key!r} in unit {unit_id}")
        return int(row["attempts"])

    def scratch_list(self, unit_id: UUID) -> list[ScratchRow]:
        """Return every pin in a unit of work, in insertion order."""
        rows = self._conn.execute(
            "SELECT * FROM pin_scratch WHERE unit_id = ? ORDER BY rowid", (str(unit_id),)
        ).fetchall()
        return [_scratch_row(row) for row in rows]

    def scratch_clear(self, unit_id: UUID) -> None:
        """Drop every pin in a unit of work."""
        self._conn.execute("DELETE FROM pin_scratch WHERE unit_id = ?", (str(unit_id),))

    def scratch_units(self) -> list[UUID]:
        """Return every unit of work that has pins in the scratch, oldest first."""
        rows = self._conn.execute(
            "SELECT unit_id FROM pin_scratch GROUP BY unit_id ORDER BY MIN(rowid)"
        ).fetchall()
        return [UUID(row["unit_id"]) for row in rows]


def _stored_event(row: sqlite3.Row) -> StoredEvent:
    envelope = Envelope(
        event_id=UUID(row["event_id"]),
        namespace=row["namespace"],
        type=row["type"],
        schema_version=row["schema_version"],
        causation_id=UUID(row["causation_id"]) if row["causation_id"] else None,
        correlation_id=UUID(row["correlation_id"]) if row["correlation_id"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    payload = cast("dict[str, JsonValue]", json.loads(row["payload"]))
    return StoredEvent(seq=row["seq"], envelope=envelope, payload=payload, hash=row["hash"])


def _scratch_row(row: sqlite3.Row) -> ScratchRow:
    return ScratchRow(
        unit_id=UUID(row["unit_id"]),
        pin_key=row["pin_key"],
        kind=row["kind"],
        result=cast("JsonValue", json.loads(row["result"])),
        attempts=row["attempts"],
    )
