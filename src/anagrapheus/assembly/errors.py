"""What prompt assembly refuses, and why each refusal is loud."""

from __future__ import annotations

__all__ = [
    "AssemblyError",
    "BudgetExceeded",
    "MislabelledPart",
    "NotGranted",
    "StaleRead",
    "UncitedSource",
]


class AssemblyError(Exception):
    """Base for refusals while assembling a context."""


class NotGranted(AssemblyError, LookupError):
    """A role asked for something its grant does not hold."""


class StaleRead(AssemblyError):
    """A slice was read while the projection under it was not at the slice's position."""


class MislabelledPart(AssemblyError, ValueError):
    """A part cites model-written prose under a register that would present it as canon."""


class UncitedSource(AssemblyError, ValueError):
    """A part cites a source its grant never handed out."""


class BudgetExceeded(AssemblyError):
    """The binding parts alone do not fit, so the call cannot carry its own invariants."""
