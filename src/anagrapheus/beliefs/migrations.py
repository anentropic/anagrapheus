"""
The belief index's own schema, versioned independently of the other components.

Same runner, same database file, its own component row — see
:mod:`anagrapheus.timeline.migrations`.
"""

from __future__ import annotations

from typing import Final

__all__ = ["COMPONENT", "LATEST_VERSION", "MIGRATIONS"]

COMPONENT: Final = "beliefs"

MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    # v1 — minted ids, and the entity -> belief direction that closes identity's seam.
    (
        """
        CREATE TABLE belief_ids (
            belief_id  TEXT    PRIMARY KEY,
            predicate  TEXT    NOT NULL,
            seq        INTEGER NOT NULL
        ) STRICT
        """,
        "CREATE INDEX ix_belief_ids_predicate ON belief_ids (predicate, seq)",
        # The whole reason this component exists. `position` keeps the argument slot so
        # a re-key can substitute in place: for an ordered predicate `reports_to(a, b)`
        # and `reports_to(b, a)` are different propositions, and a set of entities
        # could not tell them apart.
        """
        CREATE TABLE belief_entities (
            belief_id  TEXT    NOT NULL,
            entity_id  TEXT    NOT NULL,
            position   INTEGER NOT NULL,
            seq        INTEGER NOT NULL,
            PRIMARY KEY (belief_id, position)
        ) STRICT
        """,
        "CREATE INDEX ix_belief_entities_entity ON belief_entities (entity_id, seq)",
    ),
)

LATEST_VERSION: Final = len(MIGRATIONS)
