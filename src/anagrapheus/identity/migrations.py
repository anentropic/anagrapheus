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
    # identity_aliases is the row each entity shows for each form; from v2 it is
    # derived from identity_claims.
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
        # history() filters on entity_id and orders by seq; the primary key leads with
        # seq, so without this every audit lookup scans the whole decision table.
        "CREATE INDEX ix_identity_decisions_entity ON identity_decisions (entity_id, seq)",
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
    # v2 — every recorded surface form, with where it stands and the merges that
    # carried it there. identity_aliases keeps only the row each entity shows; this
    # keeps the rows behind it too, so an unmerge can find exactly what its merge moved.
    (
        """
        CREATE TABLE identity_claims (
            surface_form  TEXT    NOT NULL,
            seq           INTEGER NOT NULL,
            stance        TEXT    NOT NULL,
            entity_id     TEXT    NOT NULL,
            path          TEXT    NOT NULL,
            aside         INTEGER NOT NULL,
            PRIMARY KEY (surface_form, seq)
        ) STRICT
        """,
        "CREATE INDEX ix_identity_claims_entity ON identity_claims (entity_id, surface_form)",
        # A projection folded before this version kept no record of which merges
        # carried a row, so each row it shows is taken as its holder's own: an unmerge
        # of a merge it already holds gives nothing back until the projection is
        # rebuilt.
        """
        INSERT OR IGNORE INTO identity_claims
            (surface_form, seq, stance, entity_id, path, aside)
        SELECT surface_form, seq, stance, entity_id, '[]', 0 FROM identity_aliases
        """,
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)
