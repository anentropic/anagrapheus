"""
The identity projection's own schema, versioned independently of the timeline's.

Same runner, same database file, its own component row — see
:mod:`anagrapheus.timeline.migrations`. The projection is disposable, so these tables
could in principle be dropped and re-folded instead of migrated; they are migrated
anyway, because re-folding a long log to add a column is a cost a consumer should
choose rather than inherit.
"""

from __future__ import annotations

from typing import Final

__all__ = ["COMPONENT", "LATEST_VERSION", "MIGRATIONS"]

COMPONENT: Final = "identity"

MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    # v1 — entities, aliases, the audit trail, and the belief-layer re-key seam.
    (
        """
        CREATE TABLE identity_entities (
            entity_id        TEXT    PRIMARY KEY,
            established_seq  INTEGER NOT NULL,
            provenance       TEXT    NOT NULL,
            merged_into      TEXT,
            merged_seq       INTEGER
        ) STRICT
        """,
        """
        CREATE TABLE identity_aliases (
            entity_id     TEXT    NOT NULL,
            surface_form  TEXT    NOT NULL,
            stance        TEXT    NOT NULL,
            seq           INTEGER NOT NULL,
            PRIMARY KEY (entity_id, surface_form)
        ) STRICT
        """,
        "CREATE INDEX ix_identity_aliases_form ON identity_aliases (surface_form)",
        """
        CREATE TABLE identity_decisions (
            seq        INTEGER NOT NULL,
            entity_id  TEXT    NOT NULL,
            kind       TEXT    NOT NULL,
            detail     TEXT    NOT NULL,
            PRIMARY KEY (seq, entity_id)
        ) STRICT
        """,
        """
        CREATE TABLE identity_rekeys (
            seq          INTEGER NOT NULL,
            kind         TEXT    NOT NULL,
            from_entity  TEXT    NOT NULL,
            to_entity    TEXT    NOT NULL,
            PRIMARY KEY (seq, from_entity, to_entity)
        ) STRICT
        """,
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)
