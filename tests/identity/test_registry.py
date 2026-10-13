"""Resolution, canonicalization, the audit trail, and the belief-layer seam."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from anagrapheus.identity import (
    UNGRADED,
    AmbiguousSurfaceForm,
    MergeCycle,
    UnknownEntity,
    alias,
    establish,
    merge,
    split,
)

if TYPE_CHECKING:
    from .conftest import Rig


def test_established_entity_resolves_from_its_first_form(rig: Rig):
    entity_id, event = establish(rig.types, "alice@corp", provenance="signup")
    rig.commit(event)

    resolution = rig.registry.resolve("alice@corp")
    assert resolution.sole() == entity_id
    # Ungraded, not certain: see test_ladder for why establishing asserts nothing.
    assert resolution.candidates[0].stance == UNGRADED


def test_resolution_matches_exactly_never_fuzzily(rig: Rig):
    _, event = establish(rig.types, "Alice Smith", provenance="signup")
    rig.commit(event)

    assert rig.registry.resolve("alice smith").candidates == ()
    assert rig.registry.resolve("A. Smith").candidates == ()


def test_resolution_unifies_nfc_spellings(rig: Rig):
    entity_id, event = establish(rig.types, "Amélie", provenance="signup")
    rig.commit(event)

    assert rig.registry.resolve("Amélie").sole() == entity_id


def test_two_entities_on_one_form_stay_ambiguous(rig: Rig):
    """The layer records the collision; it does not pick a winner."""
    first, e1 = establish(rig.types, "J. Smith", provenance="import")
    second, e2 = establish(rig.types, "other", provenance="import")
    rig.commit(e1, e2, alias(rig.types, second, "J. Smith", "suspected"))

    resolution = rig.registry.resolve("J. Smith")
    assert {c.entity_id for c in resolution.candidates} == {first, second}
    with pytest.raises(AmbiguousSurfaceForm, match="matched 2 entities"):
        resolution.sole()


def test_candidates_are_ordered_strongest_stance_first(rig: Rig):
    weak, e1 = establish(rig.types, "weak-anchor", provenance="import")
    strong, e2 = establish(rig.types, "strong-anchor", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, weak, "shared", "doubted"),
        alias(rig.types, strong, "shared", "believed"),
    )

    candidates = rig.registry.resolve("shared").candidates
    assert [c.entity_id for c in candidates] == [strong, weak]


def test_merge_collapses_candidates_to_the_kept_entity(rig: Rig):
    kept, e1 = establish(rig.types, "alice@corp", provenance="signup")
    absorbed, e2 = establish(rig.types, "Alice Smith", provenance="import")
    rig.commit(e1, e2, alias(rig.types, absorbed, "alice@corp", "suspected"))
    assert len(rig.registry.resolve("alice@corp").candidates) == 2

    rig.commit(merge(rig.types, kept=kept, absorbed=absorbed))

    resolution = rig.registry.resolve("alice@corp")
    assert resolution.sole() == kept
    assert rig.registry.canonical(absorbed) == kept


def test_canonical_follows_a_chain(rig: Rig):
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    c, ec = establish(rig.types, "c", provenance="x")
    rig.commit(
        ea,
        eb,
        ec,
        merge(rig.types, kept=b, absorbed=a),
        merge(rig.types, kept=c, absorbed=b),
    )

    assert rig.registry.canonical(a) == c
    assert rig.registry.canonical(c) == c


def test_canonical_of_an_unknown_entity_is_itself(rig: Rig):
    stranger = uuid.uuid7()
    assert rig.registry.canonical(stranger) == stranger


def test_a_merge_cycle_raises_rather_than_hanging(rig: Rig):
    """
    The fold ignores a reverse merge, so only a projection folded before it did holds a cycle.

    Planted directly, as such a projection would hold it.
    """
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(ea, eb, merge(rig.types, kept=b, absorbed=a))
    rig.store.connection.execute(
        "UPDATE identity_entities SET merged_into = ? WHERE entity_id = ?", (str(a), str(b))
    )

    with pytest.raises(MergeCycle):
        rig.registry.canonical(a)


def test_history_records_every_decision_in_order(rig: Rig):
    entity_id, event = establish(rig.types, "alice@corp", provenance="signup")
    rig.commit(event, alias(rig.types, entity_id, "Alice", "believed"))

    history = rig.registry.history(entity_id)
    assert [row.kind for row in history] == ["established", "aliased"]
    assert [row.seq for row in history] == [1, 2]


def test_history_of_an_unknown_entity_raises(rig: Rig):
    with pytest.raises(UnknownEntity):
        rig.registry.history(uuid.uuid7())


def test_merge_emits_one_rekey_naming_its_target(rig: Rig):
    kept, e1 = establish(rig.types, "kept", provenance="x")
    absorbed, e2 = establish(rig.types, "absorbed", provenance="x")
    before = rig.commit(e1, e2)

    rig.commit(merge(rig.types, kept=kept, absorbed=absorbed))

    rekeys = rig.registry.rekeys_since(before)
    assert len(rekeys) == 1
    assert rekeys[0].kind == "merge"
    assert (rekeys[0].from_entity, rekeys[0].to_entity) == (absorbed, kept)


def test_split_emits_one_rekey_per_product(rig: Rig):
    """Several rows from one entity is how the layer states an ambiguous target."""
    original, event = establish(rig.types, "acme", provenance="import")
    before = rig.commit(event, alias(rig.types, original, "acme-uk", "believed"))

    left, right = uuid.uuid7(), uuid.uuid7()
    rig.commit(
        split(rig.types, original, [(left, ["acme-uk"]), (right, [])]),
    )

    rekeys = rig.registry.rekeys_since(before)
    assert {r.kind for r in rekeys} == {"split"}
    assert {r.from_entity for r in rekeys} == {original}
    assert {r.to_entity for r in rekeys} == {left, right}


def test_split_moves_the_named_forms_and_leaves_the_rest(rig: Rig):
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event, alias(rig.types, original, "acme-uk", "believed"))

    carved = uuid.uuid7()
    rig.commit(split(rig.types, original, [(carved, ["acme-uk"])]))

    assert rig.registry.resolve("acme-uk").sole() == carved
    assert rig.registry.resolve("acme").sole() == original


def test_split_carries_the_stance_of_the_form_it_moves(rig: Rig):
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event, alias(rig.types, original, "acme-uk", "doubted"))

    carved = uuid.uuid7()
    rig.commit(split(rig.types, original, [(carved, ["acme-uk"])]))

    assert rig.registry.resolve("acme-uk").candidates[0].stance == "doubted"


def test_split_leaves_the_original_canonical(rig: Rig):
    """A split is not a merge: nothing yields to anything."""
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event)
    carved = uuid.uuid7()
    rig.commit(split(rig.types, original, [(carved, [])]))

    assert rig.registry.canonical(original) == original
    assert rig.registry.canonical(carved) == carved


def test_rekeys_since_is_position_bounded(rig: Rig):
    kept, e1 = establish(rig.types, "kept", provenance="x")
    absorbed, e2 = establish(rig.types, "absorbed", provenance="x")
    rig.commit(e1, e2)
    at = rig.commit(merge(rig.types, kept=kept, absorbed=absorbed))

    assert rig.registry.rekeys_since(at) == []


def test_re_aliasing_replaces_the_stance(rig: Rig):
    entity_id, event = establish(rig.types, "anchor", provenance="x")
    rig.commit(event, alias(rig.types, entity_id, "maybe", "doubted"))
    rig.commit(alias(rig.types, entity_id, "maybe", "certain"))

    assert rig.registry.resolve("maybe").candidates[0].stance == "certain"
    assert len(rig.registry.surface_forms(entity_id)) == 2


def test_re_applying_a_split_keeps_the_stance_it_moved(rig: Rig):
    """The fold claims idempotency; a re-applied split must not downgrade a stance."""
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event, alias(rig.types, original, "acme-uk", "certain"))
    carved = uuid.uuid7()
    at = rig.commit(split(rig.types, original, [(carved, ["acme-uk"])]))

    rig.store.commit_position("anagrapheus.identity", at - 1)
    rig.projector.catch_up()

    assert rig.registry.resolve("acme-uk").candidates[0].stance == "certain"


def test_re_applying_a_split_leaves_a_later_alias_of_the_original_alone(rig: Rig):
    """The original records the form again after the split; a replay must not take it."""
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event, alias(rig.types, original, "acme-uk", "doubted"))
    carved = uuid.uuid7()
    at = rig.commit(split(rig.types, original, [(carved, ["acme-uk"])]))
    last = rig.commit(alias(rig.types, original, "acme-uk", "certain"))

    rig.store.commit_position("anagrapheus.identity", at - 1)
    rig.projector.catch_up()

    assert {(r.entity_id, r.stance, r.seq) for r in rig.registry.resolve("acme-uk").candidates} == {
        (carved, "doubted", at),
        (original, "certain", last),
    }


def test_a_split_may_not_give_one_form_to_two_entities(rig: Rig):
    """Contradictory input: a split disambiguates, so one form cannot go to both."""
    original, event = establish(rig.types, "acme", provenance="import")
    rig.commit(event, alias(rig.types, original, "acme-uk", "believed"))

    with pytest.raises(ValueError, match="one surface form to two entities"):
        split(rig.types, original, [(uuid.uuid7(), ["acme-uk"]), (uuid.uuid7(), ["acme-uk"])])


def test_a_split_after_a_merge_moves_the_absorbed_entitys_form(rig: Rig):
    """
    A split must reach forms the survivor gained by merging.

    A merge leaves the absorbed entity's alias rows behind and `resolve` canonicalizes
    them at read time, so a later split of the survivor has to reach them too —
    otherwise the form it was asked to disambiguate still matches two entities.
    """
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(e1, e2, alias(rig.types, absorbed, "acme-uk", "believed"))
    rig.commit(merge(rig.types, kept=kept, absorbed=absorbed))
    assert rig.registry.resolve("acme-uk").sole() == kept

    carved = uuid.uuid7()
    rig.commit(split(rig.types, kept, [(carved, ["acme-uk"])]))

    assert rig.registry.resolve("acme-uk").sole() == carved


def test_a_merge_gathers_the_absorbed_entitys_surface_forms(rig: Rig):
    """After a merge the absorbed id names nothing; its forms belong to the survivor."""
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(e1, e2, merge(rig.types, kept=kept, absorbed=absorbed))

    forms = {row.surface_form for row in rig.registry.surface_forms(kept)}
    assert forms == {"acme", "acme-old"}
    assert rig.registry.surface_forms(absorbed) == []
