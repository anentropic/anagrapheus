"""Withdrawing a merge: the check before append, the projection, the seam, and replay."""

from __future__ import annotations

import itertools
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
    """
    A cycle a projection already holds is no longer permanent.

    The fold now ignores a reverse merge, so the cycle is planted directly, as a
    projection folded before it did would hold it.
    """
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(ea, eb, merge(rig.types, kept=b, absorbed=a))
    rig.store.connection.execute(
        "UPDATE identity_entities SET merged_into = ? WHERE entity_id = ?", (str(a), str(b))
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


def _two_claims_carried_on(rig: Rig) -> tuple[UUID, UUID, UUID, UUID]:
    """B and C both bring "ACME" into A, then A goes into Z; returns Z, A, B, C."""
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="import")
    c, ec = establish(rig.types, "c", provenance="ledger")
    rig.commit(
        ez,
        ea,
        eb,
        ec,
        alias(rig.types, b, "ACME", "believed"),
        alias(rig.types, c, "ACME", "doubted"),
    )
    return z, a, b, c


def _forms(rig: Rig, *entity_ids: UUID) -> dict[UUID, set[tuple[str, str, int]]]:
    return {
        entity_id: {
            (r.surface_form, r.stance, r.seq) for r in rig.registry.surface_forms(entity_id)
        }
        for entity_id in entity_ids
    }


def test_unmerging_the_survivor_restores_the_claim_still_standing_on_it(rig: Rig):
    """
    B and C both bring "ACME" into A, A into Z; B out of A, then A out of Z.

    The row A into Z moved was B's, and B has it back. What A still holds the form
    for is C, so A gets C's claim back off Z, at C's sequence and with C's stance,
    and Z keeps nothing of it.
    """
    z, a, b, c = _two_claims_carried_on(rig)
    brought = _forms(rig, z, a, b, c)
    rig.commit(
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=a, absorbed=c),
        merge(rig.types, kept=z, absorbed=a),
    )
    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    rig.commit(unmerge(rig.types, rig.registry, kept=z, absorbed=a))

    assert _forms(rig, z, b) == {z: brought[z], b: brought[b]}
    # C is still merged into A, so A holds C's forms: "c", and "ACME" as C recorded it.
    assert _forms(rig, a)[a] == brought[a] | brought[c]
    assert rig.registry.canonical(c) == a
    _assert_replay_is_idempotent(rig)
    assert _forms(rig, z, b) == {z: brought[z], b: brought[b]}
    assert _forms(rig, a)[a] == brought[a] | brought[c]


@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(("b from a", "c from a", "a from z"))),
    ids=" / ".join,
)
def test_every_order_of_unmerges_gives_back_what_each_entity_brought(
    rig: Rig, order: tuple[str, ...]
):
    """Whichever way the three merges are withdrawn, each entity ends with its own forms."""
    z, a, b, c = _two_claims_carried_on(rig)
    brought = _forms(rig, z, a, b, c)
    rig.commit(
        merge(rig.types, kept=a, absorbed=b),
        merge(rig.types, kept=a, absorbed=c),
        merge(rig.types, kept=z, absorbed=a),
    )
    pairs = {"b from a": (a, b), "c from a": (a, c), "a from z": (z, a)}

    for step in order:
        kept, absorbed = pairs[step]
        rig.commit(unmerge(rig.types, rig.registry, kept=kept, absorbed=absorbed))
        # No two entities ever hold one form at the same sequence.
        rows = [row for forms in _forms(rig, z, a, b, c).values() for row in forms]
        assert len({(form, seq) for form, _stance, seq in rows}) == len(rows), step
        _assert_replay_is_idempotent(rig)

    assert _forms(rig, z, a, b, c) == brought
    for entity_id in (z, a, b, c):
        assert rig.registry.canonical(entity_id) == entity_id


def test_a_later_form_of_the_same_name_on_the_survivor_does_not_hide_the_row(rig: Rig):
    """
    B into A, A into Z, then A records "bee" itself: B out of A still finds B's row on Z.

    A's own "bee" is a later recording of the same name, not the row the merge moved,
    so it stays on A, and B takes its own row back off Z.
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
    bee = _forms(rig, z)[z]
    rig.commit(alias(rig.types, a, "bee", "certain"))
    own = _forms(rig, a)[a]

    rig.commit(unmerge(rig.types, rig.registry, kept=a, absorbed=b))

    # A still yields to Z, so its own "bee" resolves to Z.
    assert {(r.entity_id, r.stance) for r in rig.registry.resolve("bee").candidates} == {
        (z, "certain"),
        (b, "believed"),
    }
    assert _forms(rig, a)[a] == own
    assert {row for row in bee if row[0] == "bee"} <= _forms(rig, b)[b]
    assert {r.surface_form for r in rig.registry.surface_forms(z)} == {"z", "a"}
    _assert_replay_is_idempotent(rig)
    assert _forms(rig, a)[a] == own


# -- merges that name an entity nothing established ------------------------------------


def _assert_ignored(
    rig: Rig, at: int, before: dict[str, list[tuple[object, ...]]], *entities: UUID
) -> None:
    """The merge at ``at`` is in the history of every established entity, and did nothing."""
    for entity_id in entities:
        assert _kinds(rig, entity_id)[-1] == "merge_ignored"
        assert rig.registry.history(entity_id)[-1].seq == at
    assert rig.registry.rekeys_since(at - 1) == []
    after = snapshot(rig.store.connection)
    assert after["identity_entities"] == before["identity_entities"]
    assert after["identity_aliases"] == before["identity_aliases"]
    assert after["identity_rekeys"] == before["identity_rekeys"]


def test_a_merge_of_an_entity_never_established_is_a_recorded_no_op(rig: Rig):
    """
    Nothing established the absorbed id, so there is nothing for it to yield.

    Recording the merge as if it landed would emit a re-key the belief layer acts on,
    while a later unmerge of the pair is ignored because the entity was never merged.
    """
    kept, e = establish(rig.types, "acme", provenance="crm")
    ghost = uuid.uuid7()
    rig.commit(e)
    before = snapshot(rig.store.connection)

    at = rig.commit(merge(rig.types, kept=kept, absorbed=ghost, identity=rig.registry))

    _assert_ignored(rig, at, before, kept)
    assert json.loads(rig.registry.history(kept)[-1].detail)["absorbed"] == str(ghost)
    assert rig.registry.merged_into(ghost) is None
    assert rig.registry.canonical(ghost) == ghost
    _assert_replay_is_idempotent(rig)


def test_a_merge_into_an_entity_never_established_is_a_recorded_no_op(rig: Rig):
    """A survivor nothing established cannot hold what the absorbed entity names."""
    absorbed, e = establish(rig.types, "acme-old", provenance="import")
    ghost = uuid.uuid7()
    rig.commit(e)
    before = snapshot(rig.store.connection)

    at = rig.commit(merge(rig.types, kept=ghost, absorbed=absorbed, identity=rig.registry))

    _assert_ignored(rig, at, before, absorbed)
    assert rig.registry.canonical(absorbed) == absorbed
    assert rig.registry.resolve("acme-old").sole() == absorbed
    _assert_replay_is_idempotent(rig)


def test_an_entity_established_after_a_merge_named_it_is_canonical(rig: Rig):
    """Ignored is decided at the merge's own position, before the entity existed."""
    kept, e = establish(rig.types, "acme", provenance="crm")
    later = uuid.uuid7()
    rig.commit(e, merge(rig.types, kept=kept, absorbed=later))

    rig.commit(establish(rig.types, "acme-old", provenance="import", entity_id=later)[1])

    assert rig.registry.canonical(later) == later
    assert rig.registry.resolve("acme-old").sole() == later
    with pytest.raises(NotMerged):
        unmerge(rig.types, rig.registry, kept=kept, absorbed=later)


# -- merges into an entity that has itself yielded -------------------------------------


def _absorbed_survivor(rig: Rig) -> tuple[UUID, UUID, UUID]:
    """A merged into Z, and B standing alone with a form of its own."""
    z, ez = establish(rig.types, "z", provenance="x")
    a, ea = establish(rig.types, "a", provenance="x")
    b, eb = establish(rig.types, "b", provenance="x")
    rig.commit(ez, ea, eb, merge(rig.types, kept=z, absorbed=a))
    return z, a, b


def test_merge_with_the_registry_refuses_a_survivor_already_merged(rig: Rig):
    """A yields to Z, so B has to be merged into Z, where A's forms now stand."""
    z, a, b = _absorbed_survivor(rig)

    with pytest.raises(AlreadyMerged, match=f"{a} is already merged into {z}; merge into"):
        merge(rig.types, kept=a, absorbed=b, identity=rig.registry)


def test_merge_with_the_registry_still_names_a_reverse_merge_a_cycle(rig: Rig):
    """The survivor already yields, but to the absorbed entity: the cycle is the news."""
    z, a, _b = _absorbed_survivor(rig)

    with pytest.raises(MergeCycle, match="would close a cycle"):
        merge(rig.types, kept=a, absorbed=z, identity=rig.registry)


def test_a_merge_into_an_absorbed_survivor_is_a_recorded_no_op(rig: Rig):
    """
    Built by hand, past the helper's check: B stays canonical.

    Landing it would leave B yielding to A, which names nothing, and a re-key of
    beliefs on B onto A that identity resolves to Z.
    """
    z, a, b = _absorbed_survivor(rig)
    before = snapshot(rig.store.connection)

    at = rig.commit(rig.types.pending(Merged(kept=a, absorbed=b)))

    _assert_ignored(rig, at, before, a, b)
    assert json.loads(rig.registry.history(b)[-1].detail)["kept_merged_into"] == str(z)
    assert rig.registry.canonical(b) == b
    assert rig.registry.resolve("b").sole() == b
    _assert_replay_is_idempotent(rig)


def test_a_reverse_merge_that_reaches_the_log_is_ignored_not_a_cycle(rig: Rig):
    """A into Z, then Z into A: A has yielded, so it cannot take Z, and Z stays canonical."""
    z, a, _b = _absorbed_survivor(rig)

    rig.commit(rig.types.pending(Merged(kept=a, absorbed=z)))

    assert _kinds(rig, z)[-1] == "merge_ignored"
    assert rig.registry.canonical(a) == z
    assert rig.registry.canonical(z) == z


def test_a_merge_into_a_survivor_given_back_first_lands(rig: Rig):
    z, a, b = _absorbed_survivor(rig)
    rig.commit(unmerge(rig.types, rig.registry, kept=z, absorbed=a))

    rig.commit(merge(rig.types, kept=a, absorbed=b, identity=rig.registry))

    assert rig.registry.canonical(b) == a
    assert rig.registry.resolve("b").sole() == a
