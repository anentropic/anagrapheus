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

from anagrapheus.identity.errors import AlreadyMerged, MergeCycle, NotMerged
from anagrapheus.identity.events import (
    Aliased,
    Established,
    Merged,
    Split,
    SplitAssignment,
    Unmerged,
)
from anagrapheus.identity.ladder import UNGRADED
from anagrapheus.timeline.pins import pin_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from anagrapheus.identity.registry import IdentityRegistry
    from anagrapheus.timeline._canonical import Json
    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.pins import PinResolver
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = [
    "ENTITY_PIN_KIND",
    "alias",
    "establish",
    "merge",
    "mint_entity_id",
    "split",
    "unmerge",
]

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
    stance: str = UNGRADED,
    material: Mapping[str, Json] | None = None,
    resolver: PinResolver | None = None,
    correlation_id: UUID | None = None,
) -> tuple[UUID, PendingEvent]:
    """
    Declare a new entity into existence; return its id and the event to append.

    Establishing is not resolution and does not consult the registry: there is no
    external referent to match, so there is nothing to match against. Supply
    ``entity_id`` to use an id you already hold, or ``resolver`` to have it pinned —
    see :func:`mint_entity_id`.

    **With a resolver, ``material`` is load-bearing.** It defaults to
    ``{"surface_form": ..., "provenance": ...}``, and a pin key is reused for identical
    material — so two genuinely distinct records that share a name *and* a source would
    be minted as one entity, erasing the very ambiguity this layer exists to preserve.
    Whenever that is possible, pass material carrying a discriminator that identifies
    the record: the source system's own id, say. It must be **stable across runs** like
    any pin material — a counter or a timestamp would replay to a different entity and
    defeat the pinning.
    """
    if entity_id is None:
        entity_id = mint_entity_id(
            material
            if material is not None
            else {"surface_form": surface_form, "provenance": provenance},
            resolver=resolver,
        )
    event = Established(
        entity_id=entity_id,
        first_surface_form=surface_form,
        provenance=provenance,
        stance=stance,
    )
    return entity_id, registry.pending(event, correlation_id=correlation_id)


def alias(
    registry: EventTypeRegistry,
    entity_id: UUID,
    surface_form: str,
    stance: str = UNGRADED,
    *,
    correlation_id: UUID | None = None,
) -> PendingEvent:
    """Record another surface form for an entity, at a stance from your vocabulary."""
    event = Aliased(entity_id=entity_id, surface_form=surface_form, stance=stance)
    return registry.pending(event, correlation_id=correlation_id)


def merge(
    registry: EventTypeRegistry,
    *,
    kept: UUID,
    absorbed: UUID,
    source_event: UUID | None = None,
    correlation_id: UUID | None = None,
    identity: IdentityRegistry | None = None,
) -> PendingEvent:
    """
    Record that two ids name one referent.

    The correction appends: nothing is erased from the log, and undoing this is another
    event, :func:`unmerge`, rather than a deletion. In the *projection* the absorbed
    entity yields its surface forms to the survivor — it names nothing once it has
    yielded — and ``history()`` keeps the record of which entity originally recorded
    each of them.

    Pass ``identity``, the registry as it currently stands, to have the merge checked
    before it exists. If ``absorbed`` is already merged, into ``kept`` or anywhere
    else, this raises :class:`~anagrapheus.identity.errors.AlreadyMerged`: an entity
    yields to one survivor at a time, so withdraw the standing merge with
    :func:`unmerge` first. If ``kept`` already yields, directly or through a chain, to
    ``absorbed``, it raises :class:`~anagrapheus.identity.errors.MergeCycle` instead of
    returning an event that would leave both entities with no canonical id. The checks
    are only as current as the registry, so catch it up first.

    Without the checks, a merge of an entity that is already merged is folded as a
    recorded no-op: the standing merge stays, and the history of both entities named
    in the new one says it was ignored.
    """
    if identity is not None:
        current = identity.merged_into(absorbed)
        if current is not None:
            raise AlreadyMerged(
                f"cannot merge {absorbed} into {kept}: it is already merged into "
                f"{current}; unmerge it first"
            )
        _refuse_cycle(identity, kept=kept, absorbed=absorbed)
    event = Merged(kept=kept, absorbed=absorbed, source_event=source_event)
    return registry.pending(event, correlation_id=correlation_id)


def unmerge(
    registry: EventTypeRegistry,
    identity: IdentityRegistry,
    *,
    kept: UUID,
    absorbed: UUID,
    source_event: UUID | None = None,
    correlation_id: UUID | None = None,
) -> PendingEvent:
    """
    Withdraw the merge of ``absorbed`` into ``kept``.

    ``absorbed`` becomes canonical again and takes back the surface forms the merge
    moved onto ``kept``; the merge itself stays in the log and in ``history()``. The
    belief layer hears about it through ``rekeys_since`` as a choice, not a move: a
    belief keyed on ``kept`` may have been about either entity.

    Checked before append against ``identity``, the registry as it currently stands:
    if ``absorbed`` is not merged directly into ``kept``, this raises
    :class:`~anagrapheus.identity.errors.NotMerged` rather than returning an event that
    would do nothing. Catch the registry up first; an event that slips past the check
    anyway is folded as a recorded no-op.
    """
    current = identity.merged_into(absorbed)
    if current != kept:
        where = "is not merged" if current is None else f"is merged into {current}"
        raise NotMerged(f"cannot unmerge {absorbed} from {kept}: it {where}")
    event = Unmerged(kept=kept, absorbed=absorbed, source_event=source_event)
    return registry.pending(event, correlation_id=correlation_id)


def _refuse_cycle(identity: IdentityRegistry, *, kept: UUID, absorbed: UUID) -> None:
    """Raise if ``absorbed`` already lies on ``kept``'s merge chain."""
    seen = {kept}
    current: UUID | None = kept
    while (current := identity.merged_into(current)) is not None:
        if current == absorbed:
            raise MergeCycle(
                f"merging {absorbed} into {kept} would close a cycle: {kept} already "
                f"yields to {absorbed}"
            )
        if current in seen:
            raise MergeCycle(f"merge chain from {kept} already cycles at {current}")
        seen.add(current)


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
