"""
The chronicle's own schema, versioned independently of the other components.

Same runner, same database file, its own component row — see
:mod:`anagrapheus.timeline.migrations`.
"""

from __future__ import annotations

from typing import Final

__all__ = ["COMPONENT", "LATEST_VERSION", "MIGRATIONS"]

COMPONENT: Final = "chronicle"

MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    # v1 -- sections, append-only, with the covered range as the load-bearing column.
    (
        """
        CREATE TABLE chronicle_sections (
            section_id  TEXT    PRIMARY KEY,
            from_seq    INTEGER NOT NULL,
            to_seq      INTEGER NOT NULL,
            unit        INTEGER NOT NULL,
            supersedes  TEXT,
            prose       TEXT    NOT NULL,
            pin         TEXT
        ) STRICT
        """,
        # Range lookup is the chronicle's whole read pattern: "what covers seq N", and
        # "what covers everything below the tail".
        "CREATE INDEX ix_chronicle_range ON chronicle_sections (from_seq, to_seq)",
        # Resolving a range to its current head walks the supersession chain backwards,
        # so the reverse direction is the one that needs the index.
        "CREATE INDEX ix_chronicle_supersedes ON chronicle_sections (supersedes)",
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)
