"""
Which event types mean what, so the fold never depends on the application's names.

The stream's vocabulary belongs to whoever owns the domain, and identity is the first
library layer that wants to emit domain-shaped events. The rule — *the library ships the
shape, the application owns the name* — is honoured here by binding each of the four
identity **roles** to an event type rather than hard-coding a class: a consumer that
already emits ``EntityEstablished`` binds that and never sees a library type.

The default binds this module's own models under the reserved ``"anagrapheus"``
namespace, so an application with no opinion gets a working layer. Same relationship
``promote_as`` has with the pin ledger, one layer up.
"""

# Design: note 02; upstream agent-core doc 00 §8.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Self

from anagrapheus.identity.events import Aliased, Established, Merged, Split
from anagrapheus.timeline.registry import RESERVED_NAMESPACE, EventKey

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from anagrapheus.timeline.envelope import DomainEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["IdentityRole", "IdentityVocabulary", "RoleBinding", "register_builtin_events"]

type IdentityDecision = Established | Aliased | Merged | Split


class IdentityRole(StrEnum):
    """The four decisions an identity layer records."""

    ESTABLISHED = "established"
    ALIASED = "aliased"
    MERGED = "merged"
    SPLIT = "split"


@dataclass(frozen=True)
class RoleBinding:
    """
    One role, the event type that carries it, and how to read that type.

    ``adapt`` converts the application's decoded payload into this library's shape, so
    everything downstream of the vocabulary speaks one vocabulary. For the default
    bindings it is the identity function.

    The binding names ``(namespace, type)`` and deliberately not a schema version:
    payloads reach the fold already upcast to the latest registered version, so a
    version bump needs no change here.
    """

    namespace: str
    type: str
    adapt: Callable[[DomainEvent], IdentityDecision]


def _expect[E: DomainEvent](model: type[E]) -> Callable[[DomainEvent], E]:
    """Adapter for a binding whose event type is already the library's shape."""

    def adapt(event: DomainEvent) -> E:
        if not isinstance(event, model):
            raise TypeError(f"expected {model.__name__}, got {type(event).__name__}")
        return event

    return adapt


_DEFAULT_MODELS: Final[Mapping[IdentityRole, type[DomainEvent]]] = {
    IdentityRole.ESTABLISHED: Established,
    IdentityRole.ALIASED: Aliased,
    IdentityRole.MERGED: Merged,
    IdentityRole.SPLIT: Split,
}


def register_builtin_events(registry: EventTypeRegistry) -> None:
    """Register this library's own identity events under the reserved namespace."""
    for role, model in _DEFAULT_MODELS.items():
        registry._register(EventKey(RESERVED_NAMESPACE, role.value, 1), model)  # noqa: SLF001


class IdentityVocabulary:
    """Maps event types to identity roles, and their payloads to this library's shape."""

    def __init__(self, bindings: Mapping[IdentityRole, RoleBinding]) -> None:
        missing = set(IdentityRole) - set(bindings)
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
        Bind this module's own models, under the reserved namespace.

        Pair with :func:`register_builtin_events` on the same registry — the binding
        says what an event *means*, the registration says how to decode it.
        """
        return cls(
            {
                role: RoleBinding(RESERVED_NAMESPACE, role.value, _expect(model))
                for role, model in _DEFAULT_MODELS.items()
            }
        )

    def role_for(self, namespace: str, type: str) -> IdentityRole | None:
        """Return the role this event type carries, or ``None`` if it carries none."""
        return self._roles.get((namespace, type))

    def adapt(self, role: IdentityRole, event: DomainEvent) -> IdentityDecision:
        """Convert a decoded payload into this library's shape for ``role``."""
        return self._bindings[role].adapt(event)
