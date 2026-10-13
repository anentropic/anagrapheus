"""The identity fold: dispatch, indifference to foreign events, and replay invariance."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.identity import (
    Aliased,
    IdentityFold,
    IdentityRole,
    IdentityVocabulary,
    Merged,
    RoleBinding,
    Split,
    Unmerged,
    alias,
    establish,
    merge,
    register_builtin_events,
    split,
)
from anagrapheus.identity.fold import FOLD_NAME
from anagrapheus.identity.vocabulary import _expect  # noqa: PLC2701
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    EventTypeRegistry,
    Projector,
    UpcastRegistry,
)
from anagrapheus.timeline.registry import RESERVED_NAMESPACE

from .conftest import Rig, build_rig

if TYPE_CHECKING:
    import sqlite3

    from anagrapheus.timeline import PendingEvent

_TABLES = ("identity_entities", "identity_aliases", "identity_decisions", "identity_rekeys")


class Unrelated(DomainEvent, frozen=True):
    """A domain event the identity layer has no business folding."""

    note: str


def snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    """The whole projection, as comparable rows."""
    return {
        table: sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in _TABLES
    }


def test_fold_declares_the_protocol_shape(rig: Rig):
    assert rig.fold.name == FOLD_NAME


def test_fold_ignores_events_outside_its_vocabulary(rig: Rig):
    rig.types.event("demo", "Unrelated")(Unrelated)
    entity_id, event = establish(rig.types, "alice", provenance="signup")
    rig.commit(event, rig.types.pending(Unrelated(note="not mine")))

    assert rig.registry.resolve("alice").sole() == entity_id
    assert len(rig.registry.history(entity_id)) == 1


def test_catch_up_resumes_above_the_position(rig: Rig):
    entity_id, event = establish(rig.types, "alice", provenance="signup")
    rig.commit(event)
    rig.commit(alias(rig.types, entity_id, "Alice", "believed"))
    rig.projector.catch_up()  # nothing new: must not re-apply

    assert [row.kind for row in rig.registry.history(entity_id)] == ["established", "aliased"]


def test_re_applying_a_batch_is_idempotent(rig: Rig):
    """The fold contract permits the last batch to be re-applied after a crash."""
    _, event = establish(rig.types, "alice", provenance="signup")
    rig.commit(event)
    before = snapshot(rig.store.connection)

    rig.store.commit_position(FOLD_NAME, 0)
    rig.projector.catch_up()

    assert snapshot(rig.store.connection) == before


def test_rebuild_equals_incremental(rig: Rig):
    first, e1 = establish(rig.types, "alice", provenance="signup")
    second, e2 = establish(rig.types, "alicia", provenance="import")
    rig.commit(
        e1,
        e2,
        alias(rig.types, second, "Alice S", "suspected"),
        merge(rig.types, kept=first, absorbed=second),
    )
    incremental = snapshot(rig.store.connection)

    rig.rebuild()

    assert snapshot(rig.store.connection) == incremental


def test_a_second_fold_name_keeps_its_own_position(rig: Rig):
    """Two identity folds over one file are independent projections."""
    other = IdentityFold(rig.identity_store, rig.types, name="identity.shadow")
    _, event = establish(rig.types, "alice", provenance="signup")
    rig.commit(event)

    assert rig.store.read_position(FOLD_NAME) == 1
    assert rig.store.read_position(other.name) == 0


def _script(ops: list[tuple[int, int, int]], types: EventTypeRegistry) -> list[PendingEvent]:
    """Turn opcodes into a valid decision sequence over a growing entity set."""
    events: list[PendingEvent] = []
    entities: list[UUID] = []
    merges: list[tuple[UUID, UUID]] = []
    counter = 0
    for kind, a, b in ops:
        counter += 1
        if kind == 0 or len(entities) < 2:
            entity_id = UUID(int=counter)
            entities.append(entity_id)
            events.append(
                establish(types, f"form-{counter}", provenance="p", entity_id=entity_id)[1]
            )
        elif kind == 1:
            target = entities[a % len(entities)]
            events.append(alias(types, target, f"form-{b % 5}", "believed"))
        elif kind == 2:
            keep, drop = a % len(entities), b % len(entities)
            if keep == drop:
                continue
            events.append(merge(types, kept=entities[keep], absorbed=entities[drop]))
            merges.append((entities[keep], entities[drop]))
        elif kind == 4:
            # Built by hand, past the helper's check: an unmerge of an earlier merge,
            # which may or may not still hold by the time it is folded, so the no-op
            # path is exercised as well as the real one.
            if merges:
                kept, absorbed = merges[a % len(merges)]
            else:
                kept, absorbed = entities[0], entities[1]
            events.append(types.pending(Unmerged(kept=kept, absorbed=absorbed)))
        else:
            original = entities[a % len(entities)]
            counter += 1
            carved = UUID(int=counter)
            entities.append(carved)
            events.append(split(types, original, [(carved, [f"form-{b % 5}"])]))
    return events


def opcodes(
    max_size: int = 24, kinds: st.SearchStrategy[int] | None = None, min_size: int = 0
) -> st.SearchStrategy[list[tuple[int, int, int]]]:
    """Histories as opcodes: a decision kind, then two numbers it picks its subjects by."""
    return st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=4) if kinds is None else kinds,
            st.integers(min_value=0, max_value=9),
            st.integers(min_value=0, max_value=9),
        ),
        min_size=min_size,
        max_size=max_size,
    )


@settings(max_examples=25, deadline=None)
@given(
    ops=opcodes(),
    batch_size=st.integers(min_value=1, max_value=5),
    split_at=st.integers(min_value=0, max_value=10),
)
def test_fold_replay_invariance(
    tmp_path_factory: pytest.TempPathFactory,
    ops: list[tuple[int, int, int]],
    batch_size: int,
    split_at: int,
):
    """Straight-through == appended-in-two-goes == rebuilt. Same log, same registry."""
    root = tmp_path_factory.mktemp("identity-prop")

    # The script is a deterministic function of `ops` — entity ids and surface forms
    # are derived from the opcode positions — so each rig gets an equivalent log.
    straight = build_rig(root / "straight.db")
    script = _script(ops, straight.types)
    if script:
        straight.append(*script)
    straight.projector.catch_up(batch_size=batch_size)
    expected = snapshot(straight.store.connection)
    straight.store.close()

    two_go = build_rig(root / "twogo.db")
    script = _script(ops, two_go.types)
    if script[:split_at]:
        two_go.append(*script[:split_at])
    two_go.projector.catch_up(batch_size=batch_size)
    if script[split_at:]:
        two_go.append(*script[split_at:])
    two_go.projector.catch_up(batch_size=batch_size)
    assert snapshot(two_go.store.connection) == expected

    two_go.rebuild()
    assert snapshot(two_go.store.connection) == expected
    two_go.store.close()


def test_fold_rejects_a_vocabulary_that_adapts_to_the_wrong_shape(rig: Rig):
    """A binding that lies about its role is a loud error, not silent corruption."""
    types = EventTypeRegistry()
    register_builtin_events(types)
    # Type-correct and still wrong: the adapter returns a valid decision for the wrong
    # role, which no signature can catch. Hence the runtime guard.
    liar = Merged(kept=UUID(int=1), absorbed=UUID(int=2))
    bindings = {
        IdentityRole.ESTABLISHED: RoleBinding(
            RESERVED_NAMESPACE, "established", lambda _event: liar
        ),
        IdentityRole.ALIASED: RoleBinding(RESERVED_NAMESPACE, "aliased", _expect(Aliased)),
        IdentityRole.MERGED: RoleBinding(RESERVED_NAMESPACE, "merged", _expect(Merged)),
        IdentityRole.SPLIT: RoleBinding(RESERVED_NAMESPACE, "split", _expect(Split)),
    }
    fold = IdentityFold(rig.identity_store, types, vocabulary=IdentityVocabulary(bindings))
    _, event = establish(types, "alice", provenance="signup")
    rig.store.append([event])

    projector = Projector(rig.store, EventReader(rig.store, UpcastRegistry(), types), [fold])
    with pytest.raises(TypeError, match="vocabulary adapted"):
        projector.catch_up()
