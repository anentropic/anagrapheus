"""Withdrawing a merge: the check before append, the projection, the seam, and replay."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from anagrapheus.identity import (
    AlreadyMerged,
    MergeCycle,
    Merged,
    NotMerged,
    Unmerged,
    alias,
    establish,
    merge,
    split,
    unmerge,
)
from anagrapheus.identity.fold import FOLD_NAME

from .test_fold import snapshot

if TYPE_CHECKING:
    from uuid import UUID

    from .conftest import Rig


def _pair(rig: Rig) -> tuple[UUID, UUID]:
    """Two entities, the second with a form of its own, merged into the first."""
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, absorbed, "acme-uk", "believed"),
        merge(rig.types, kept=kept, absorbed=absorbed),
    )
    return kept, absorbed


def _kinds(rig: Rig, entity_id: UUID) -> list[str]:
    return [row.kind for row in rig.registry.history(entity_id)]


# -- the cycle check on merge ----------------------------------------------------------


def test_merge_with_the_registry_refuses_a_reverse_merge(rig: Rig):
    kept, absorbed = _pair(rig)

    with pytest.raises(MergeCycle, match="would close a cycle"):
        merge(rig.types, kept=absorbed, absorbed=kept, identity=rig.registry)


def test_merge_with_the_registry_refuses_a_cycle_through_a_chain(rig: Rig):
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

    with pytest.raises(MergeCycle, match="would close a cycle"):
        merge(rig.types, kept=a, absorbed=c, identity=rig.registry)


def test_merge_with_the_registry_allows_a_merge_that_closes_nothing(rig: Rig):
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    c, ec = establish(rig.types, "c", provenance="x")
    rig.commit(ea, eb, ec, merge(rig.types, kept=b, absorbed=a))

    rig.commit(merge(rig.types, kept=c, absorbed=b, identity=rig.registry))

    assert rig.registry.canonical(a) == c


def test_merge_with_the_registry_refuses_an_entity_already_merged(rig: Rig):
    """B is in A; merging B into C as well has to withdraw the first merge first."""
    kept, absorbed = _pair(rig)
    other, e = establish(rig.types, "other", provenance="x")
    rig.commit(e)

    with pytest.raises(AlreadyMerged, match=f"already merged into {kept}; unmerge it first"):
        merge(rig.types, kept=other, absorbed=absorbed, identity=rig.registry)


def test_merge_with_the_registry_refuses_the_same_merge_twice(rig: Rig):
    kept, absorbed = _pair(rig)

    with pytest.raises(AlreadyMerged, match="unmerge it first"):
        merge(rig.types, kept=kept, absorbed=absorbed, identity=rig.registry)


# -- the check on unmerge --------------------------------------------------------------


def test_unmerge_refuses_a_pair_that_is_not_merged(rig: Rig):
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(ea, eb)

    with pytest.raises(NotMerged, match="is not merged"):
        unmerge(rig.types, rig.registry, kept=a, absorbed=b)


def test_unmerge_refuses_the_pair_the_wrong_way_round(rig: Rig):
    kept, absorbed = _pair(rig)

    with pytest.raises(NotMerged, match="is not merged"):
        unmerge(rig.types, rig.registry, kept=absorbed, absorbed=kept)


def test_unmerge_refuses_a_merge_that_is_only_indirect(rig: Rig):
    """An unmerge withdraws one merge; A into B into C is not a merge of A into C."""
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

    with pytest.raises(NotMerged, match=f"is merged into {b}"):
        unmerge(rig.types, rig.registry, kept=c, absorbed=a)


def test_an_unmerge_may_not_name_one_entity_twice():
    entity_id = uuid.uuid7()
    with pytest.raises(ValueError, match="unmerged from itself"):
        Unmerged(kept=entity_id, absorbed=entity_id)


# -- what the projection does ----------------------------------------------------------


def test_unmerge_restores_resolution_of_both_entities_forms(rig: Rig):
    kept, absorbed = _pair(rig)
    assert rig.registry.resolve("acme-uk").sole() == kept

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert rig.registry.canonical(absorbed) == absorbed
    assert rig.registry.merged_into(absorbed) is None
    assert rig.registry.resolve("acme").sole() == kept
    assert rig.registry.resolve("acme-old").sole() == absorbed
    assert rig.registry.resolve("acme-uk").sole() == absorbed
    assert {r.surface_form for r in rig.registry.surface_forms(kept)} == {"acme"}
    assert {r.surface_form for r in rig.registry.surface_forms(absorbed)} == {
        "acme-old",
        "acme-uk",
    }
    # The stance the absorbed entity recorded comes back with the form.
    assert rig.registry.resolve("acme-uk").candidates[0].stance == "believed"


def test_a_form_both_entities_recorded_stays_on_both(rig: Rig):
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, kept, "ACME", "certain"),
        alias(rig.types, absorbed, "ACME", "doubted"),
        merge(rig.types, kept=kept, absorbed=absorbed),
    )
    assert rig.registry.resolve("ACME").sole() == kept

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    candidates = rig.registry.resolve("ACME").candidates
    assert {(c.entity_id, c.stance) for c in candidates} == {
        (kept, "certain"),
        (absorbed, "doubted"),
    }


def test_a_form_the_survivor_recorded_after_the_merge_stays_on_it_too(rig: Rig):
    kept, absorbed = _pair(rig)
    rig.commit(alias(rig.types, kept, "acme-uk", "certain"))

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert {c.entity_id for c in rig.registry.resolve("acme-uk").candidates} == {
        kept,
        absorbed,
    }


def test_a_form_a_later_split_moved_elsewhere_stays_where_it_went(rig: Rig):
    """The split was its own judgment about that form; the unmerge does not undo it."""
    kept, absorbed = _pair(rig)
    carved = uuid.uuid7()
    rig.commit(split(rig.types, kept, [(carved, ["acme-uk"])]))

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert rig.registry.resolve("acme-uk").sole() == carved
    assert rig.registry.resolve("acme-old").sole() == absorbed


def test_an_entity_merged_into_the_absorbed_one_comes_back_with_it(rig: Rig):
    """X into B, then B into A. Withdrawing B from A gives B back X's forms too."""
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    x, ex = establish(rig.types, "x", provenance="x")
    rig.commit(
        ea,
        eb,
        ex,
        merge(rig.types, kept=b, absorbed=x),
        merge(rig.types, kept=a, absorbed=b),
    )
    assert rig.registry.resolve("x").sole() == a

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert rig.registry.canonical(x) == b
    assert rig.registry.resolve("x").sole() == b
    assert rig.registry.resolve("a").sole() == a


def test_an_unmerge_breaks_a_merge_cycle(rig: Rig):
    """A cycle appended without the check is no longer permanent."""
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(
        ea,
        eb,
        merge(rig.types, kept=b, absorbed=a),
        merge(rig.types, kept=a, absorbed=b),
    )
    with pytest.raises(MergeCycle):
        rig.registry.resolve("a")

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert rig.registry.canonical(a) == b
    assert rig.registry.canonical(b) == b
    assert rig.registry.resolve("a").sole() == b
    assert rig.registry.resolve("b").sole() == b


def test_history_keeps_the_merge_and_records_the_unmerge(rig: Rig):
    kept, absorbed = _pair(rig)
    at = rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert _kinds(rig, kept) == ["established", "merged", "unmerged"]
    assert _kinds(rig, absorbed) == ["established", "aliased", "absorbed", "unmerged"]
    last = rig.registry.history(absorbed)[-1]
    assert last.seq == at
    assert json.loads(last.detail)["restored"] == ["acme-old", "acme-uk"]


def test_an_unmerge_matching_no_merge_is_a_recorded_no_op(rig: Rig):
    """
    Built by hand, past the helper's check: the fold records it and changes nothing.

    Raising would leave the projection stuck on an event that is already canon.
    """
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(ea, eb)
    before = snapshot(rig.store.connection)

    at = rig.commit(rig.types.pending(Unmerged(kept=a, absorbed=b)))

    assert rig.store.read_position(FOLD_NAME) == at
    assert _kinds(rig, a) == ["established", "unmerge_ignored"]
    assert _kinds(rig, b) == ["established", "unmerge_ignored"]
    assert json.loads(rig.registry.history(b)[-1].detail)["merged_into"] is None
    after = snapshot(rig.store.connection)
    assert after["identity_entities"] == before["identity_entities"]
    assert after["identity_aliases"] == before["identity_aliases"]
    assert after["identity_rekeys"] == before["identity_rekeys"]


def test_a_second_unmerge_of_the_same_pair_is_a_no_op(rig: Rig):
    kept, absorbed = _pair(rig)
    pending = unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed)
    rig.commit(pending)
    once = rig.registry.surface_forms(absorbed)

    rig.commit(rig.types.pending(Unmerged(kept=kept, absorbed=absorbed)))

    assert rig.registry.surface_forms(absorbed) == once
    assert _kinds(rig, absorbed)[-1] == "unmerge_ignored"


def test_a_merge_after_an_unmerge_merges_again(rig: Rig):
    kept, absorbed = _pair(rig)
    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    rig.commit(merge(rig.types, kept=kept, absorbed=absorbed, identity=rig.registry))

    assert rig.registry.resolve("acme-uk").sole() == kept
    assert rig.registry.surface_forms(absorbed) == []


# -- the belief-layer seam -------------------------------------------------------------


def test_unmerge_emits_split_shaped_rekeys_from_the_survivor(rig: Rig):
    kept, absorbed = _pair(rig)
    before = rig.store.last_seq()

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    rekeys = rig.registry.rekeys_since(before)
    assert {r.kind for r in rekeys} == {"unmerge"}
    assert {r.from_entity for r in rekeys} == {kept}
    assert {r.to_entity for r in rekeys} == {kept, absorbed}
    assert len({r.seq for r in rekeys}) == 1


def test_a_no_op_unmerge_emits_no_rekeys(rig: Rig):
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    before = rig.commit(ea, eb)

    rig.commit(rig.types.pending(Unmerged(kept=a, absorbed=b)))

    assert rig.registry.rekeys_since(before) == []


# -- replay ----------------------------------------------------------------------------


def _story(rig: Rig) -> None:
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, kept, "ACME", "certain"),
        alias(rig.types, absorbed, "ACME", "doubted"),
        alias(rig.types, absorbed, "acme-uk", "believed"),
        merge(rig.types, kept=kept, absorbed=absorbed),
    )
    rig.commit(
        alias(rig.types, kept, "acme-uk", "suspected"),
        unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed),
    )
    rig.commit(rig.types.pending(Unmerged(kept=kept, absorbed=absorbed)))
    rig.commit(merge(rig.types, kept=kept, absorbed=absorbed))


def test_rebuild_reproduces_a_history_with_unmerges(rig: Rig):
    _story(rig)
    incremental = snapshot(rig.store.connection)

    rig.rebuild()

    assert snapshot(rig.store.connection) == incremental


def test_re_applying_merges_and_unmerges_is_idempotent(rig: Rig):
    """The fold contract permits a batch to be re-applied; an unmerge must survive it."""
    _story(rig)
    expected = snapshot(rig.store.connection)

    for position in range(rig.store.last_seq()):
        rig.store.commit_position(FOLD_NAME, position)
        rig.projector.catch_up()
        assert snapshot(rig.store.connection) == expected, position


def _assert_replay_is_idempotent(rig: Rig) -> None:
    """Rewinding to every earlier position and catching up changes nothing."""
    expected = snapshot(rig.store.connection)
    for position in range(rig.store.last_seq()):
        rig.store.commit_position(FOLD_NAME, position)
        rig.projector.catch_up()
        assert snapshot(rig.store.connection) == expected, position
    rig.rebuild()
    assert snapshot(rig.store.connection) == expected


def test_re_applying_a_merge_leaves_a_later_alias_of_the_absorbed_entity_alone(rig: Rig):
    """B into A, then a form recorded on B: re-applying the merge must not move it."""
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    absorbed, e2 = establish(rig.types, "acme-old", provenance="import")
    rig.commit(e1, e2, merge(rig.types, kept=kept, absorbed=absorbed))
    rig.commit(alias(rig.types, absorbed, "acme-new", "believed"))

    _assert_replay_is_idempotent(rig)
    assert {r.surface_form for r in rig.registry.surface_forms(kept)} == {"acme", "acme-old"}


def test_an_unmerge_re_applied_takes_back_only_what_the_merge_moved(rig: Rig):
    """A form B recorded while merged was never moved to A, so A never holds it."""
    kept, absorbed = _pair(rig)
    rig.commit(alias(rig.types, absorbed, "acme-new", "believed"))
    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))
    assert rig.registry.resolve("acme-new").sole() == absorbed

    _assert_replay_is_idempotent(rig)
    assert rig.registry.resolve("acme-new").sole() == absorbed


def test_unmerging_one_entity_keeps_a_form_another_merge_also_brought(rig: Rig):
    """B and C both bring a form into A; withdrawing B leaves A holding C's claim."""
    kept, e1 = establish(rig.types, "acme", provenance="crm")
    b, e2 = establish(rig.types, "ACME", provenance="import")
    c, e3 = establish(rig.types, "ACME", provenance="ledger")
    rig.commit(
        e1,
        e2,
        e3,
        merge(rig.types, kept=kept, absorbed=b),
        merge(rig.types, kept=kept, absorbed=c),
    )

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=b))

    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {kept, b}
    _assert_replay_is_idempotent(rig)
    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {kept, b}

    # And withdrawing C too leaves A with nothing of the form.
    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=c))
    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {b, c}
    _assert_replay_is_idempotent(rig)


# -- a merge of an entity already merged -----------------------------------------------


def test_a_second_merge_of_an_absorbed_entity_is_a_recorded_no_op(rig: Rig):
    """
    Built by hand, past the helper's check: the first merge stands.

    Re-pointing would leave the forms the first merge moved on the first survivor,
    where no unmerge of the second merge could reach them.
    """
    kept, absorbed = _pair(rig)
    other, e = establish(rig.types, "other", provenance="x")
    rig.commit(e)
    before = snapshot(rig.store.connection)
    seq_before = rig.store.last_seq()

    at = rig.commit(rig.types.pending(Merged(kept=other, absorbed=absorbed)))

    assert rig.registry.canonical(absorbed) == kept
    assert rig.registry.resolve("acme-uk").sole() == kept
    assert _kinds(rig, absorbed)[-1] == "merge_ignored"
    assert _kinds(rig, other)[-1] == "merge_ignored"
    assert json.loads(rig.registry.history(absorbed)[-1].detail)["merged_into"] == str(kept)
    assert rig.registry.history(absorbed)[-1].seq == at
    assert rig.registry.rekeys_since(seq_before) == []
    after = snapshot(rig.store.connection)
    assert after["identity_entities"] == before["identity_entities"]
    assert after["identity_aliases"] == before["identity_aliases"]
    assert after["identity_rekeys"] == before["identity_rekeys"]


def test_unmerging_after_an_ignored_second_merge_restores_the_names(rig: Rig):
    kept, absorbed = _pair(rig)
    other, e = establish(rig.types, "other", provenance="x")
    rig.commit(e, rig.types.pending(Merged(kept=other, absorbed=absorbed)))

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert rig.registry.canonical(absorbed) == absorbed
    assert rig.registry.resolve("acme-uk").sole() == absorbed
    assert rig.registry.resolve("acme-old").sole() == absorbed
    assert {r.surface_form for r in rig.registry.surface_forms(kept)} == {"acme"}
    _assert_replay_is_idempotent(rig)
    assert rig.registry.resolve("acme-uk").sole() == absorbed


def test_the_same_merge_twice_then_unmerged_restores_the_names(rig: Rig):
    kept, absorbed = _pair(rig)
    rig.commit(rig.types.pending(Merged(kept=kept, absorbed=absorbed)))
    assert _kinds(rig, absorbed)[-1] == "merge_ignored"

    rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))

    assert rig.registry.resolve("acme-uk").sole() == absorbed
    _assert_replay_is_idempotent(rig)
    assert rig.registry.resolve("acme-uk").sole() == absorbed


def test_a_merge_after_the_first_is_withdrawn_lands(rig: Rig):
    """Ignored is decided at the merge's own position: B into A, out, then into C."""
    kept, absorbed = _pair(rig)
    other, e = establish(rig.types, "other", provenance="x")
    rig.commit(
        e,
        rig.types.pending(Merged(kept=other, absorbed=absorbed)),
        unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed),
    )

    rig.commit(merge(rig.types, kept=other, absorbed=absorbed, identity=rig.registry))

    assert rig.registry.canonical(absorbed) == other
    assert rig.registry.resolve("acme-uk").sole() == other
    _assert_replay_is_idempotent(rig)
    assert rig.registry.resolve("acme-uk").sole() == other


# -- an unmerge after the survivor was itself merged on --------------------------------


def test_an_unmerge_after_the_survivor_was_merged_on_restores_the_names(rig: Rig):
    """
    B into A, then A into Z, then B out of A: B's forms come back off Z.

    A later merge of the survivor carried the forms on, which is not a later judgment
    about them, so the unmerge follows them there. Withdrawing A from Z afterwards
    gives A back only its own.
    """
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(
        ez,
        ea,
        eb,
        alias(rig.types, b, "bee", "believed"),
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=z, absorbed=a),
    )
    assert rig.registry.resolve("bee").sole() == z

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert rig.registry.canonical(b) == b
    assert rig.registry.canonical(a) == z
    assert rig.registry.resolve("b").sole() == b
    assert rig.registry.resolve("bee").sole() == b
    assert rig.registry.resolve("bee").candidates[0].stance == "believed"
    assert {r.surface_form for r in rig.registry.surface_forms(z)} == {"z", "a"}
    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}
    _assert_replay_is_idempotent(rig)
    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}

    rig.commit(unmerge(rig.types, rig.registry, kept=z, absorbed=a))

    assert rig.registry.canonical(a) == a
    assert {r.surface_form for r in rig.registry.surface_forms(z)} == {"z"}
    assert {r.surface_form for r in rig.registry.surface_forms(a)} == {"a"}
    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}
    _assert_replay_is_idempotent(rig)
    assert {r.surface_form for r in rig.registry.surface_forms(a)} == {"a"}
    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}


def test_an_unmerge_follows_the_forms_through_more_than_one_later_merge(rig: Rig):
    """B into A, A into Z, Z into W: withdrawing B from A takes its forms back off W."""
    w, ew = establish(rig.types, "w", provenance="x")
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(
        ew,
        ez,
        ea,
        eb,
        alias(rig.types, b, "bee", "believed"),
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=z, absorbed=a),
        merge(rig.types, kept=w, absorbed=z),
    )

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}
    assert {r.surface_form for r in rig.registry.surface_forms(w)} == {"w", "z", "a"}
    _assert_replay_is_idempotent(rig)
    assert {r.surface_form for r in rig.registry.surface_forms(b)} == {"b", "bee"}


def test_a_form_split_off_after_the_survivor_was_merged_on_stays_where_it_went(rig: Rig):
    """B into A, A into Z, then Z split: the split's judgment stands against the unmerge."""
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    carved = uuid.uuid7()
    rig.commit(
        ez,
        ea,
        eb,
        alias(rig.types, b, "bee", "believed"),
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=z, absorbed=a),
        split(rig.types, z, [(carved, ["bee"])]),
    )

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert rig.registry.resolve("bee").sole() == carved
    assert rig.registry.resolve("b").sole() == b
    _assert_replay_is_idempotent(rig)
    assert rig.registry.resolve("bee").sole() == carved


def test_a_form_the_later_survivor_held_itself_stays_on_it_and_comes_back(rig: Rig):
    """Z recorded "bee" too, so A into Z kept Z's row; B still gets its own back."""
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(
        ez,
        ea,
        eb,
        alias(rig.types, z, "bee", "certain"),
        alias(rig.types, b, "bee", "believed"),
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=z, absorbed=a),
    )

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    candidates = rig.registry.resolve("bee").candidates
    assert {(c.entity_id, c.stance) for c in candidates} == {(z, "certain"), (b, "believed")}
    _assert_replay_is_idempotent(rig)

    rig.commit(unmerge(rig.types, rig.registry, kept=z, absorbed=a))

    candidates = rig.registry.resolve("bee").candidates
    assert {(c.entity_id, c.stance) for c in candidates} == {(z, "certain"), (b, "believed")}
    assert {r.surface_form for r in rig.registry.surface_forms(a)} == {"a"}
    _assert_replay_is_idempotent(rig)
    assert {r.surface_form for r in rig.registry.surface_forms(a)} == {"a"}


def test_a_form_another_merge_also_brought_stays_with_the_later_survivor(rig: Rig):
    """B and C both bring a form into A, A into Z: withdrawing B leaves C's claim on Z."""
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "ACME", provenance="import")
    c, ec = establish(rig.types, "ACME", provenance="ledger")
    rig.commit(
        ez,
        ea,
        eb,
        ec,
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=a, absorbed=c),
        merge(rig.types, kept=z, absorbed=a),
    )

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {z, b}
    _assert_replay_is_idempotent(rig)

    # Withdrawing C too takes the form off Z, which held it only for C.
    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=c))
    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {b, c}
    _assert_replay_is_idempotent(rig)

    # And withdrawing A from Z gives A nothing of the form.
    rig.commit(unmerge(rig.types, rig.registry, kept=z, absorbed=a))
    assert {r.entity_id for r in rig.registry.resolve("ACME").candidates} == {b, c}
    _assert_replay_is_idempotent(rig)
