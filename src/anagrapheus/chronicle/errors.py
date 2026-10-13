"""What the chronicle refuses, and why."""

from __future__ import annotations

__all__ = ["ChronicleError", "ChronicleFork", "SectionCollision", "UnknownSection"]


class ChronicleError(Exception):
    """Base for every error the chronicle raises."""


class UnknownSection(ChronicleError, LookupError):
    """A section id names no section in the store."""


class SectionCollision(ChronicleError):
    """
    A section with this id is already stored.

    Raised by the strict append path. The fold's own append treats a collision as a
    replayed batch and keeps the existing row; a deliberate write treats it as a
    mistake, since keeping the existing row would silently drop the new prose.
    """


class ChronicleFork(ChronicleError):
    """
    A write would leave two current sections over one range of the log.

    The chronicle's current sections partition what it has consolidated: each seq is
    covered by at most one of them. A section that supersedes one already superseded,
    or that overlaps a current section it does not supersede, would break that, and
    every read of "the section covering this event" would have two answers. So would a
    successor over a different range than the section it supersedes: the old range
    would still count as consolidated with no current section covering all of it.
    """
