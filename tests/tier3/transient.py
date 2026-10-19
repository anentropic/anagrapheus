"""
Retrying a live check when the provider, not the library, failed.

A tier-3 run talks to a real provider, and a shared pool (OpenRouter's free models
especially) sometimes answers 502 "provider returned an empty response" or 429 for a
request that would succeed a moment later. Failing the release gate on that tests the
pool's weather, not anagrapheus. So a check that fails with a transient HTTP status is
run again, a few times, each in a fresh directory so no attempt sees another's store.
Any other failure, and the last transient one, is raised as is.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic_ai.exceptions import ModelHTTPError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: Statuses a provider returns when the same request may succeed if sent again.
TRANSIENT = frozenset({408, 429, 500, 502, 503, 504})

ATTEMPTS = 3


def run_retrying_transient(
    check: Callable[[Path], None],
    tmp_path: Path,
    *,
    attempts: int = ATTEMPTS,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    """Run ``check``, again after a transient provider error, up to ``attempts`` times."""
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, not {attempts}")
    for attempt in range(1, attempts + 1):
        directory = tmp_path / f"attempt-{attempt}"
        directory.mkdir()
        try:
            check(directory)
        except ModelHTTPError as exc:
            if exc.status_code not in TRANSIENT or attempt == attempts:
                raise
            pause(2.0**attempt)
        else:
            return
