"""
Which event type means "a belief id was minted", so the fold never depends on a name.

One role rather than identity's four, but the same rule from doc 00 §8 — *the library
ships the shape, the application owns the name*. An application that already emits its
own minting event binds that type and never sees a library one.

The default binds this module's model under the reserved ``"anagrapheus"`` namespace,
so a consumer with no opinion gets a working index.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Self

from anagrapheus.beliefs.events import BeliefMinted
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from anagrapheus.timeline.envelope import DomainEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["BeliefRole", "BeliefVocabulary", "RoleBinding", "register_builtin_events"]


class BeliefRole(StrEnum):
    """The one thing the belief index folds."""

    MINTED = "BeliefMinted"


@dataclass(frozen=True)
class RoleBinding:
    """
    One role, the event type that carries it, and how to read that type.

    ``adapt`` converts the application's decoded payload into this library's shape.
    Named ``(namespace, type)`` and deliberately not a schema version: payloads reach
    the fold already upcast.
    """

    namespace: str
    type: str
    adapt: Callable[[DomainEvent], BeliefMinted]


def _expect(event: DomainEvent) -> BeliefMinted:
    """Adapter for the binding whose event type is already the library's shape."""
    if not isinstance(event, BeliefMinted):
        raise TypeError(f"expected BeliefMinted, got {type(event).__name__}")
    return event


_DEFAULT_MODELS: Final[Mapping[BeliefRole, type[DomainEvent]]] = {
    BeliefRole.MINTED: BeliefMinted,
}


def register_builtin_events(registry: EventTypeRegistry) -> None:
    """Register this library's own belief events under the reserved namespace."""
    for role, model in _DEFAULT_MODELS.items():
        registry._register(EventKey(RESERVED_NAMESPACE, role.value, 1), model)  # noqa: SLF001


class BeliefVocabulary:
    """Maps an event type to the minting role, and its payload to this library's shape."""

    def __init__(self, bindings: Mapping[BeliefRole, RoleBinding]) -> None:
        missing = set(BeliefRole) - set(bindings)
        if missing:
            names = ", ".join(sorted(role.value for role in missing))
            raise ValueError(f"vocabulary is missing a binding for: {names}")
        self._bindings = dict(bindings)
        self._roles = {(b.namespace, b.type): role for role, b in bindings.items()}
        if len(self._roles) != len(bindings):
            raise ValueError("two roles are bound to the same event type")

    @classmethod
    def default(cls) -> Self:
        """
        Bind this module's own model, under the reserved namespace.

        Pair with :func:`register_builtin_events` on the same registry — the binding
        says what an event *means*, the registration says how to decode it.
        """
        return cls(
            {role: RoleBinding(RESERVED_NAMESPACE, role.value, _expect) for role in _DEFAULT_MODELS}
        )

    def role_for(self, namespace: str, type: str) -> BeliefRole | None:
        """Return the role this event type carries, or ``None`` if it carries none."""
        return self._roles.get((namespace, type))

    def adapt(self, role: BeliefRole, event: DomainEvent) -> BeliefMinted:
        """Convert a decoded payload into this library's shape for ``role``."""
        return self._bindings[role].adapt(event)
