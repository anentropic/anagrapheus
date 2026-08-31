"""Wiring for the negotiation example's tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from examples.negotiation.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def table(tmp_path: Path) -> Iterator[Table]:
    """
    A table with two parties, wired as `Table.open` wires it.

    Nothing is patched here. It used to be: both folds had to be re-assembled by every
    entry point because `Table.open` installed only the identity fold, which is exactly
    the smell that says a composition root is incomplete.
    """
    seated = Table.open(tmp_path / "negotiation.db", "eu", "uk")
    try:
        yield seated
    finally:
        seated.close()
