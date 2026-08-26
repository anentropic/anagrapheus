"""The vocabulary: library defaults, and an application keeping its own event names."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from anagrapheus.identity import (
    Established,
    IdentityFold,
    IdentityRegistry,
    IdentityRole,
    IdentityVocabulary,
    RoleBinding,
    SQLiteIdentityStore,
    alias,
    establish,
    register_builtin_events,
)
from anagrapheus.identity.vocabulary import _expect  # noqa: PLC2701
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)
from anagrapheus.timeline.registry import RESERVED_NAMESPACE

from .conftest import build_rig

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import Rig


class EntityEstablished(DomainEvent, frozen=True):
    """An application's own establish event — the collision doc 00 §8 warns about."""

    ref: UUID
    label: str
    origin: str


class NameObserved(DomainEvent, frozen=True):
    """An application's own alias event, with its own field names."""

    ref: UUID
    label: str
    confidence: str


def test_default_binds_the_library_models():
    vocabulary = IdentityVocabulary.default()
    assert vocabulary.role_for(RESERVED_NAMESPACE, "established") is IdentityRole.ESTABLISHED
    assert vocabulary.role_for("demo", "established") is None


def test_a_missing_binding_is_rejected():
    with pytest.raises(ValueError, match="missing a binding for: aliased, merged, split"):
        IdentityVocabulary(
            {
                IdentityRole.ESTABLISHED: RoleBinding(
                    RESERVED_NAMESPACE, "established", _expect(Established)
                )
            }
        )


def test_two_roles_on_one_event_type_are_rejected():
    binding = RoleBinding("demo", "same", _expect(Established))
    with pytest.raises(ValueError, match="same event type"):
        IdentityVocabulary(dict.fromkeys(IdentityRole, binding))


def test_library_events_use_the_reserved_namespace(rig: Rig):
    _, event = establish(rig.types, "alice", provenance="signup")
    assert event.envelope.namespace == RESERVED_NAMESPACE
    assert event.envelope.type == "established"


def test_an_application_keeps_its_own_event_names(tmp_path: Path):
    """The whole point of §8: NVM binds EntityEstablished and never sees a library type."""
    types = EventTypeRegistry()
    types.event("nvm", "EntityEstablished")(EntityEstablished)
    types.event("nvm", "NameObserved")(NameObserved)
    register_builtin_events(types)  # merge/split still use the library shapes here

    vocabulary = IdentityVocabulary(
        {
            IdentityRole.ESTABLISHED: RoleBinding(
                "nvm",
                "EntityEstablished",
                lambda event: Established(
                    entity_id=_field_uuid(event, "ref"),
                    first_surface_form=_field_str(event, "label"),
                    provenance=_field_str(event, "origin"),
                ),
            ),
            IdentityRole.ALIASED: RoleBinding(
                "nvm",
                "NameObserved",
                lambda event: _aliased(event),
            ),
            IdentityRole.MERGED: RoleBinding(RESERVED_NAMESPACE, "merged", _expect_merged()),
            IdentityRole.SPLIT: RoleBinding(RESERVED_NAMESPACE, "split", _expect_split()),
        }
    )

    store = SQLiteEventStore(tmp_path / "nvm.db")
    try:
        identity_store = SQLiteIdentityStore(store.connection)
        fold = IdentityFold(identity_store, types, vocabulary=vocabulary)
        projector = Projector(store, EventReader(store, UpcastRegistry(), types), [fold])
        registry = IdentityRegistry(identity_store)

        ref = UUID(int=7)
        store.append(
            [
                types.pending(EntityEstablished(ref=ref, label="alice@corp", origin="genesis")),
                types.pending(NameObserved(ref=ref, label="Alice", confidence="believed")),
            ]
        )
        projector.catch_up()

        assert registry.resolve("alice@corp").sole() == ref
        assert registry.resolve("Alice").candidates[0].stance == "believed"
        assert [row.kind for row in registry.history(ref)] == ["established", "aliased"]
    finally:
        store.close()


def test_the_two_vocabularies_fold_to_the_same_state(tmp_path: Path):
    """Same decisions under different names produce an identical registry."""
    from .test_fold import snapshot

    default_rig = build_rig(tmp_path / "default.db")
    ref = UUID(int=7)
    try:
        _, event = establish(default_rig.types, "alice@corp", provenance="genesis", entity_id=ref)
        default_rig.commit(event, alias(default_rig.types, ref, "Alice", "believed"))
        expected = snapshot(default_rig.store.connection)
    finally:
        default_rig.store.close()

    types = EventTypeRegistry()
    types.event("nvm", "EntityEstablished")(EntityEstablished)
    types.event("nvm", "NameObserved")(NameObserved)
    register_builtin_events(types)
    vocabulary = IdentityVocabulary(
        {
            IdentityRole.ESTABLISHED: RoleBinding(
                "nvm",
                "EntityEstablished",
                lambda event: Established(
                    entity_id=_field_uuid(event, "ref"),
                    first_surface_form=_field_str(event, "label"),
                    provenance=_field_str(event, "origin"),
                ),
            ),
            IdentityRole.ALIASED: RoleBinding("nvm", "NameObserved", lambda e: _aliased(e)),
            IdentityRole.MERGED: RoleBinding(RESERVED_NAMESPACE, "merged", _expect_merged()),
            IdentityRole.SPLIT: RoleBinding(RESERVED_NAMESPACE, "split", _expect_split()),
        }
    )
    store = SQLiteEventStore(tmp_path / "nvm.db")
    try:
        identity_store = SQLiteIdentityStore(store.connection)
        fold = IdentityFold(identity_store, types, vocabulary=vocabulary)
        projector = Projector(store, EventReader(store, UpcastRegistry(), types), [fold])
        store.append(
            [
                types.pending(EntityEstablished(ref=ref, label="alice@corp", origin="genesis")),
                types.pending(NameObserved(ref=ref, label="Alice", confidence="believed")),
            ]
        )
        projector.catch_up()
        assert snapshot(store.connection) == expected
    finally:
        store.close()


# -- adapters, kept out of the tests above so those read as wiring ---------------------


def _field_uuid(event: DomainEvent, name: str) -> UUID:
    value = getattr(event, name)
    assert isinstance(value, UUID)
    return value


def _field_str(event: DomainEvent, name: str) -> str:
    value = getattr(event, name)
    assert isinstance(value, str)
    return value


def _aliased(event: DomainEvent):
    from anagrapheus.identity import Aliased

    return Aliased(
        entity_id=_field_uuid(event, "ref"),
        surface_form=_field_str(event, "label"),
        stance=_field_str(event, "confidence"),
    )


def _expect_merged():
    from anagrapheus.identity import Merged

    return _expect(Merged)


def _expect_split():
    from anagrapheus.identity import Split

    return _expect(Split)
