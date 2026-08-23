"""Timeline error types."""

from __future__ import annotations

__all__ = [
    "ConcurrencyError",
    "DuplicateEventType",
    "TimelineError",
    "UnknownEventType",
    "UpcastGap",
]


class TimelineError(Exception):
    """Base class for all timeline errors."""


class ConcurrencyError(TimelineError):
    """
    ``append``'s ``expected_last_seq`` was stale.

    With one fully serialized writer this is a bug tripwire, not a coordination
    mechanism: two interleaved writers indicate a broken composition, not contention
    to be retried.
    """


class DuplicateEventType(TimelineError):
    """An event key or payload model was registered more than once."""


class UnknownEventType(TimelineError):
    """A payload was read whose event key has no registered model."""


class UpcastGap(TimelineError):
    """
    An upcast chain stops short of the latest registered schema version.

    Raised loudly at read rather than silently decoding a stale shape.
    """
