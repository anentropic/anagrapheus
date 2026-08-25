"""
Building the identity decisions, ready to append.

These helpers return a ``PendingEvent`` rather than appending one. An identity
decision almost always accompanies the domain event that occasioned it — you
establish an entity *because* something happened — and the caller owns that batch and
its transaction. Handing back an event keeps identity out of the write path.

They serve the default vocabulary. An application that binds its own event types
already owns those names and constructs its own payloads; what it should still reach
for is :func:`mint_entity_id`, because the replay hazard it addresses is not a
property of the names.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from uuid import UUID

from anagrapheus.identity.events import (
    Aliased,
    Established,
    Merged,
    Split,
    SplitAssignment,
    Stance,
)
from anagrapheus.timeline.pins import pin_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from anagrapheus.timeline._canonical import Json
    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.pins import PinResolver
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["ENTITY_PIN_KIND", "alias", "establish", "merge", "mint_entity_id", "split"]

ENTITY_PIN_KIND = "entity"


def mint_entity_id(
    material: Mapping[str, Json],
    *,
    resolver: PinResolver | None = None,
    kind: str = ENTITY_PIN_KIND,
) -> UUID:
    """
    Mint an id for a newly established entity, pinned when a resolver is given.

    **Minting consumes entropy**, which makes it exactly the kind of effect §4a says
    to key on. Under generation-replay an unpinned mint draws a fresh id, and every
    belief, pin and reference keyed on the original silently orphans — the same
    failure mode as pin-key drift, reached from the other direction.

    Pass a ``resolver`` and the id is drawn through the pin ledger under ``material``,
    so a replayed run re-establishes the *same* entity. Without one this is a plain
    ``uuid7`` and a re-run will not agree with the original: correct for a live run
    that will never be replayed, and quietly wrong for one that will.

    ``material`` is the application's canonicalization policy for entity identity, and
    the same discipline applies as to any pin key — structured data, never prose.
    """
    if resolver is None:
        return uuid.uuid7()
    return resolver.resolve(
        pin_key(kind, material),
        kind=kind,
        effect=lambda: str(uuid.uuid7()),
        interpret=UUID,
    )


def establish(
    registry: EventTypeRegistry,
    surface_form: str,
    *,
    provenance: str,
    entity_id: UUID | None = None,
    resolver: PinResolver | None = None,
    correlation_id: UUID | None = None,
) -> tuple[UUID, PendingEvent]:
    """
    Declare a new entity into existence; return its id and the event to append.

    Establishing is not resolution and does not consult the registry: there is no
    external referent to match, so there is nothing to match against. Supply
    ``entity_id`` to use an id you already hold, or ``resolver`` to have it pinned —
    see :func:`mint_entity_id`.
    """
    if entity_id is None:
        entity_id = mint_entity_id(
            {"surface_form": surface_form, "provenance": provenance},
            resolver=resolver,
        )
    event = Established(
        entity_id=entity_id,
        first_surface_form=surface_form,
        provenance=provenance,
    )
    return entity_id, registry.pending(event, correlation_id=correlation_id)


def alias(
    registry: EventTypeRegistry,
    entity_id: UUID,
    surface_form: str,
    stance: Stance,
    *,
    correlation_id: UUID | None = None,
) -> PendingEvent:
    """Record another surface form for an entity, at a stance."""
    event = Aliased(entity_id=entity_id, surface_form=surface_form, stance=stance)
    return registry.pending(event, correlation_id=correlation_id)


def merge(
    registry: EventTypeRegistry,
    *,
    kept: UUID,
    absorbed: UUID,
    source_event: UUID | None = None,
    correlation_id: UUID | None = None,
) -> PendingEvent:
    """
    Record that two ids name one referent.

    The correction appends; nothing about the absorbed entity is erased, and undoing
    this is another event rather than a deletion.
    """
    event = Merged(kept=kept, absorbed=absorbed, source_event=source_event)
    return registry.pending(event, correlation_id=correlation_id)


def split(
    registry: EventTypeRegistry,
    original: UUID,
    into: Sequence[tuple[UUID, Sequence[str]]],
    *,
    source_event: UUID | None = None,
    correlation_id: UUID | None = None,
) -> PendingEvent:
    """
    Record that one id named several referents, carving out the new ones.

    ``into`` pairs each new entity id with the surface forms that move to it. Forms
    left unnamed stay with ``original``, which survives.
    """
    event = Split(
        original=original,
        into=[
            SplitAssignment(entity_id=entity_id, surface_forms=list(forms))
            for entity_id, forms in into
        ],
        source_event=source_event,
    )
    return registry.pending(event, correlation_id=correlation_id)
