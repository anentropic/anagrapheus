"""The package surface, and registering every event the library emits."""

from __future__ import annotations

import pytest

import anagrapheus
from anagrapheus import identity, timeline
from anagrapheus.timeline import EventTypeRegistry
from anagrapheus.timeline.errors import UnknownEventType
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey


def test_import():
    assert hasattr(anagrapheus, "__all__")


def _registered(registry: EventTypeRegistry) -> set[str]:
    types: set[str] = set()
    for candidate in ("PinPromoted", "PinsAbandoned", "ReplayDiverged", *identity.IdentityRole):
        name = str(candidate)
        try:
            registry.decode(EventKey(RESERVED_NAMESPACE, name, 1), {})
        except UnknownEventType:
            continue
        except ValueError:  # registered, but the empty payload fails validation
            types.add(name)
        else:  # pragma: no cover - every library payload has required fields
            types.add(name)
    return types


def test_top_level_registers_every_subpackages_events():
    registry = EventTypeRegistry()
    anagrapheus.register_builtin_events(registry)

    assert _registered(registry) == {
        "PinPromoted",
        "PinsAbandoned",
        "ReplayDiverged",
        "established",
        "aliased",
        "merged",
        "split",
    }


def test_registering_one_subpackage_leaves_the_others_undecodable():
    """The failure the top-level helper exists to prevent."""
    registry = EventTypeRegistry()
    timeline.register_builtin_events(registry)

    assert "PinPromoted" in _registered(registry)
    assert "established" not in _registered(registry)
    with pytest.raises(UnknownEventType):
        registry.decode(EventKey(RESERVED_NAMESPACE, "established", 1), {})


def test_the_helper_is_not_idempotent():
    """Duplicate registration is a loud import-time error, by design."""
    registry = EventTypeRegistry()
    anagrapheus.register_builtin_events(registry)
    with pytest.raises(Exception, match="already registered"):
        anagrapheus.register_builtin_events(registry)
