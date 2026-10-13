"""
Generated histories of identity decisions, checked against a plain model of unmerge.

The model, written out in :func:`worlds`, is what an unmerge is meant to do: every
entity ends up holding exactly what it would hold had each withdrawn merge never
happened. A split is the one decision an unmerge does not reach past, so the model
keeps the split's judgment as it was made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from anagrapheus.identity import UNGRADED, alias, establish, merge, split, unmerge

from .conftest import build_rig
from .test_fold import opcodes
from .test_unmerge import _assert_replay_is_idempotent  # noqa: PLC2701

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from anagrapheus.timeline import PendingEvent

    from .conftest import Rig

# Few entities and few names, so that names collide and merges chain.
FORMS = ("acme", "ACME", "acme-uk")
STANCES = ("doubted", "believed", "certain")
MAX_ENTITIES = 5
# Weighted towards merges and unmerges, so that unmerges after a chain of merges are
# common rather than rare: 0 establish, 1 alias, 2 merge, 3 split, 4 unmerge.
KINDS = st.sampled_from((0, 1, 1, 2, 2, 2, 2, 3, 4, 4, 4))

#: A recording of a form, named as the projection names it: the form, and the
#: position that recorded it.
Token = tuple[str, int]


@dataclass(frozen=True)
class Decision:
    """
    One identity decision, at position ``index + 1`` in the log.

    ``subject`` is the entity established or aliased, the absorbed entity of a merge
    or an unmerge, or the original of a split. ``other`` is the survivor of a merge or
    an unmerge, or the new entity of a split. An unmerge names the merge it withdraws.
    """

    kind: str
    subject: UUID
    other: UUID | None = None
    form: str = ""
    stance: str = UNGRADED
    withdraws: int | None = None


def history(ops: Sequence[tuple[int, int, int]]) -> list[Decision]:
    """
    Turn opcodes into a valid history: every merge and unmerge would pass the checks.

    A merge never takes an entity already merged and never closes a cycle; an unmerge
    always withdraws a merge that stands. Aliases may name any entity, merged or not.
    """
    entities: list[UUID] = []
    standing: dict[UUID, tuple[UUID, int]] = {}  # absorbed -> (kept, index of the merge)
    decisions: list[Decision] = []

    def reaches(start: UUID, target: UUID) -> bool:
        current = start
        while current != target:
            if current not in standing:
                return False
            current = standing[current][0]
        return True

    for kind, a, b in ops:
        n = len(entities)
        if n < 3 or (kind == 0 and n < MAX_ENTITIES):
            entity = UUID(int=n + 1)
            entities.append(entity)
            decisions.append(
                Decision("establish", entity, form=FORMS[b % 3], stance=STANCES[a % 3])
            )
        elif kind in {0, 1}:
            subject = entities[a % n]
            decisions.append(
                Decision("alias", subject, form=FORMS[b % 3], stance=STANCES[(a + b) % 3])
            )
        elif kind == 2:
            kept, absorbed = entities[a % n], entities[b % n]
            if absorbed in standing or reaches(kept, absorbed):
                continue
            standing[absorbed] = (kept, len(decisions))
            decisions.append(Decision("merge", absorbed, kept))
        elif kind == 4:
            if not standing:
                continue
            absorbed = sorted(standing)[a % len(standing)]
            kept, index = standing.pop(absorbed)
            decisions.append(Decision("unmerge", absorbed, kept, withdraws=index))
        elif n < MAX_ENTITIES:
            carved = UUID(int=n + 1)
            entities.append(carved)
            decisions.append(Decision("split", entities[a % n], carved, form=FORMS[b % 3]))
    return decisions


@dataclass
class World:
    """Who holds which recordings. On each entity, a form's first recording is the row shown."""

    stacks: dict[UUID, dict[str, list[Token]]] = field(default_factory=dict)
    stance: dict[Token, str] = field(default_factory=dict)
    merged_into: dict[UUID, UUID] = field(default_factory=dict)

    def claims(self, entity: UUID, form: str) -> list[Token]:
        return self.stacks.setdefault(entity, {}).setdefault(form, [])

    def record(self, entity: UUID, form: str, stance: str, seq: int) -> None:
        self.claims(entity, form).insert(0, (form, seq))
        self.stance[form, seq] = stance

    def merge(self, absorbed: UUID, kept: UUID) -> None:
        for form, claims in self.stacks.pop(absorbed, {}).items():
            self.claims(kept, form).extend(claims)
        self.merged_into[absorbed] = kept

    def remove(self, token: Token) -> None:
        for forms in self.stacks.values():
            if token in forms.get(token[0], []):
                forms[token[0]].remove(token)

    def tokens(self, entity: UUID) -> set[Token]:
        return {token for claims in self.stacks.get(entity, {}).values() for token in claims}

    def shown(self) -> dict[UUID, set[tuple[str, str, int]]]:
        return {
            entity: {
                (form, self.stance[claims[0]], claims[0][1])
                for form, claims in forms.items()
                if claims
            }
            for entity, forms in self.stacks.items()
        }


def worlds(decisions: Sequence[Decision]) -> list[World]:
    """
    The world after each prefix of ``decisions``: the model of what unmerge means.

    The world after a history is the history replayed with every withdrawn merge left
    out, and every unmerge with it. In that replay:

    - an establish or an alias puts a new recording first on its entity's claims to
      the form, so an entity shows its newest recording;
    - a merge puts everything on the absorbed entity after what the survivor already
      has, so the survivor's own row wins, and the earliest merge's row wins next;
    - a split keeps the judgment it made in the history as it happened, the world just
      before it: it takes the very recording the original showed then, wherever that
      recording is in the replay, gives the new entity a recording of its own at that
      stance, and puts aside what stood behind it then on the original and is still
      there in the replay, shown by no one.
    """
    out: list[World] = [World()]
    for end in range(1, len(decisions) + 1):
        prefix = decisions[:end]
        withdrawn = {d.withdraws for d in prefix if d.kind == "unmerge"}
        world = World()
        for index, decision in enumerate(prefix):
            seq = index + 1
            if decision.kind in {"establish", "alias"}:
                world.record(decision.subject, decision.form, decision.stance, seq)
            elif decision.kind == "merge" and index not in withdrawn:
                assert decision.other is not None
                world.merge(decision.subject, decision.other)
            elif decision.kind == "split":
                assert decision.other is not None
                before = out[index].claims(decision.subject, decision.form)
                stance = UNGRADED
                if before:
                    world.remove(before[0])
                    stance = out[index].stance[before[0]]
                behind = set(before[1:])
                world.stacks.setdefault(decision.subject, {})[decision.form] = [
                    token
                    for token in world.claims(decision.subject, decision.form)
                    if token not in behind
                ]
                world.record(decision.other, decision.form, stance, seq)
        out.append(world)
    return out


def _pending(rig: Rig, decision: Decision) -> PendingEvent:
    """The decision as the helpers build it, with the registry checking each one."""
    match decision.kind:
        case "establish":
            return establish(
                rig.types,
                decision.form,
                provenance="p",
                entity_id=decision.subject,
                stance=decision.stance,
            )[1]
        case "alias":
            return alias(rig.types, decision.subject, decision.form, decision.stance)
        case "merge":
            assert decision.other is not None
            return merge(
                rig.types, kept=decision.other, absorbed=decision.subject, identity=rig.registry
            )
        case "unmerge":
            assert decision.other is not None
            return unmerge(rig.types, rig.registry, kept=decision.other, absorbed=decision.subject)
        case _:
            assert decision.other is not None
            return split(rig.types, decision.subject, [(decision.other, [decision.form])])


def _shown(rig: Rig, entities: set[UUID]) -> dict[UUID, set[tuple[str, str, int]]]:
    return {
        entity: {(r.surface_form, r.stance, r.seq) for r in rig.registry.surface_forms(entity)}
        for entity in entities
    }


def _rows(shown: dict[UUID, set[tuple[str, str, int]]]) -> set[tuple[UUID, Token]]:
    return {(entity, (form, seq)) for entity, rows in shown.items() for form, _, seq in rows}


@settings(max_examples=400, deadline=None)
@given(ops=opcodes(min_size=10, max_size=30, kinds=KINDS))
def test_unmerge_matches_the_model(
    tmp_path_factory: pytest.TempPathFactory, ops: list[tuple[int, int, int]]
):
    """
    After every decision of a generated history, the projection is the model's world.

    Checked at each step: each entity shows exactly the rows the model says, and yields
    to the entity it says; no form is shown twice at one position; and an unmerge moves
    only what its merge moved, taking nothing else off any entity and bringing nothing
    else back. At the end, re-applying any suffix of the log and rebuilding it change
    nothing.
    """
    decisions = history(ops)
    expected = worlds(decisions)
    rig = build_rig(tmp_path_factory.mktemp("unmerge-model") / "identity.db")
    entities: set[UUID] = set()
    try:
        for index, decision in enumerate(decisions):
            entities |= {decision.subject} | ({decision.other} if decision.other else set())
            before = _shown(rig, entities)
            assert rig.commit(_pending(rig, decision)) == index + 1

            world = expected[index + 1]
            after = _shown(rig, entities)
            assert after == {e: world.shown().get(e, set()) for e in entities}, (index, decision)
            for entity in entities:
                assert rig.registry.merged_into(entity) == world.merged_into.get(entity)
            rows = rig.store.connection.execute(
                "SELECT surface_form, seq FROM identity_aliases"
            ).fetchall()
            assert len({tuple(row) for row in rows}) == len(rows)

            if decision.kind == "unmerge":
                assert decision.withdraws is not None
                moved = expected[decision.withdraws].tokens(decision.subject)
                left = _rows(before) - _rows(after)
                arrived = _rows(after) - _rows(before)
                assert {t for e, t in left if e != decision.subject} <= moved
                assert {t for e, t in arrived if e == decision.subject} <= moved
        _assert_replay_is_idempotent(rig)
    finally:
        rig.store.close()
