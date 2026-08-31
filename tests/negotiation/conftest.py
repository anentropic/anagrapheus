"""Wiring for the negotiation example's tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from examples.negotiation.table import Table

from anagrapheus.beliefs import BeliefIndexFold, SQLiteBeliefIndexStore
from anagrapheus.identity import IdentityFold
from anagrapheus.timeline import Projector

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def table(tmp_path: Path) -> Iterator[Table]:
    """
    A table with two parties, and **both** folds running.

    The belief index fold is not optional here: `plan_rekeys` reads what was minted
    from that index, so a table without it reports no plans and a merge silently
    misses every belief.
    """
    seated = Table.open(tmp_path / "negotiation.db", "eu", "uk")
    index = SQLiteBeliefIndexStore(seated.store.connection)
    seated.index_store = index
    seated.projector = Projector(
        seated.store,
        seated.reader,
        [IdentityFold(seated.identity, seated.types), BeliefIndexFold(index, seated.types)],
    )
    try:
        yield seated
    finally:
        seated.close()
