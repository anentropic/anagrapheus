"""Event-sourced timeline, belief store, and identity layer for long-running agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import beliefs, identity, timeline

if TYPE_CHECKING:
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["beliefs", "identity", "register_builtin_events", "timeline"]


def register_builtin_events(registry: EventTypeRegistry) -> None:
    """
    Register every event type this library emits, across all its subpackages.

    Under-registering is a live failure mode rather than an untidiness: an application
    that uses the pin ledger and the identity layer over one stream, but registers only
    one subpackage's events, gets an
    :class:`~anagrapheus.timeline.errors.UnknownEventType` the first time a reader
    reaches the other's. Library events all live under the reserved namespace and can
    never collide with an application's own, so registering them all is free.

    The per-subpackage functions remain for a consumer using only one of them.
    """
    timeline.register_builtin_events(registry)
    identity.register_builtin_events(registry)
    beliefs.register_builtin_events(registry)
