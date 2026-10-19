"""The tier-3 retry: transient provider errors are retried, everything else is not."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from tests.tier3.transient import run_retrying_transient

if TYPE_CHECKING:
    from pathlib import Path


class Flaky:
    """A check that fails with the given statuses, in order, then passes."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.directories: list[Path] = []

    def __call__(self, directory: Path) -> None:
        self.directories.append(directory)
        if self.statuses:
            raise ModelHTTPError(self.statuses.pop(0), "test-model")


def test_a_transient_error_is_retried_in_a_fresh_directory(tmp_path: Path):
    check = Flaky(502, 429)
    pauses: list[float] = []

    run_retrying_transient(check, tmp_path, pause=pauses.append)

    assert len(check.directories) == 3
    assert len(set(check.directories)) == 3
    assert pauses == [2.0, 4.0]


def test_the_last_transient_error_is_raised(tmp_path: Path):
    check = Flaky(502, 502, 502)

    with pytest.raises(ModelHTTPError):
        run_retrying_transient(check, tmp_path, pause=lambda _: None)
    assert len(check.directories) == 3


def test_a_client_error_is_not_retried(tmp_path: Path):
    check = Flaky(401)

    with pytest.raises(ModelHTTPError):
        run_retrying_transient(check, tmp_path, pause=lambda _: None)
    assert len(check.directories) == 1
