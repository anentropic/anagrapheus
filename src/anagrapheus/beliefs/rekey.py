"""
The other side of identity's re-key seam.

The payoff of the seam: a merge means every belief keyed on the absorbed entity is now
suspect, and the belief layer turns that into a list of states to re-key. The identity
layer states the consequence as ``(kind, from_entity, to_entity)`` rows and performs no
cascade — it does not know what a belief is. This module knows what a belief *id* is,
which is enough to say which ones are affected and what they would become.

**It plans; it does not apply.** Re-keying a belief is an AGM operation with real
semantics: whether the old id is contracted or left standing, what happens when the
new id already carries a belief, whose stance survives a collision. Those are
judgments about belief, and this library owns the id and not the belief. The plan is
handed to the caller with everything computed and nothing decided.
"""

# Design: note 02; note 03; upstream agent-core doc 00 §5d.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anagrapheus.beliefs.errors import BeliefError, MergeCycleInBatch, UnrenderableBelief
from anagrapheus.beliefs.predicates import MintedId

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from anagrapheus.beliefs.index import BeliefIndex
    from anagrapheus.beliefs.predicates import PredicateRegistry
    from anagrapheus.identity.store import RekeyRow

__all__ = ["BeliefRekey", "plan_rekeys"]

#: One identity decision: ``(seq, kind, from_entity)``.
type _Key = tuple[int, str, UUID]

#: Rows gathered by the decision that emitted them.
type _Grouped = dict[_Key, list[UUID]]

#: A decision that moves an entity on: ``(seq, kind, where it goes)``.
type _Step = tuple[int, str, UUID]


@dataclass(frozen=True, slots=True)
class _Batch:
    """
    A batch's decisions, as far as the batch itself can tell what each one did.

    ``decisions`` holds the ones that landed. ``withdrawn`` names each merge a later
    unmerge in the batch gave back. ``holders`` names, for each unmerge, the entities
    whose beliefs it re-keys, when that is not simply the entity its rows come from.

    ``steps`` and ``branches`` remember what following an entity through the batch
    found, so each walk is made once per plan. The two sides of an unmerge can meet
    again, and walked afresh every time they meet, the walks double with each unmerge.
    """

    decisions: _Grouped
    withdrawn: frozenset[_Key]
    holders: dict[_Key, tuple[UUID, ...]]
    steps: dict[tuple[UUID, int, bool], _Step | None] = field(default_factory=dict)
    branches: dict[tuple[UUID, int], tuple[UUID, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BeliefRekey:
    """
    One belief the identity decision at ``seq`` left keyed on a superseded entity.

    ``candidates`` holds the id (or ids) the belief would be re-minted as:

    - after a **merge**, exactly one — the absorbed entity yielded to a single
      survivor, so the successor is computed rather than guessed;
    - after a **split**, one per product — which product the belief belongs to depends
      on what the belief *says*, and neither this library nor identity may interpret
      that. Several candidates state exactly what is known.
    - after an **unmerge**, two — where the kept entity stands and the id it gave back.
      A belief keyed on the kept entity, or on whatever it has since been merged into,
      may have been about either all along, which is the split's question again. One,
      if the same batch merges the pair again; more, if a later unmerge in the batch
      gives an entity back from one of them.

    ``sole()`` is for the merge case, and raises rather than picking so the assumption
    stays visible.
    """

    seq: int
    kind: str
    belief_id: str
    from_entity: UUID
    candidates: tuple[str, ...]

    def sole(self) -> str:
        """The single successor id, or raise if the decision left more than one."""
        if len(self.candidates) != 1:
            article = "an" if self.kind[:1] in set("aeiou") else "a"
            raise ValueError(
                f"belief {self.belief_id!r} has {len(self.candidates)} candidate successors "
                f"after {article} {self.kind}; which one it belongs to is not this layer's "
                f"judgment"
            )
        return self.candidates[0]


def plan_rekeys(
    rekeys: Iterable[RekeyRow],
    index: BeliefIndex,
    predicates: PredicateRegistry,
    *,
    skip_unrenderable: bool = False,
) -> list[BeliefRekey]:
    """
    Turn identity's re-key rows into the beliefs they affect and what they become.

    Rows sharing a ``(seq, from_entity)`` are one decision with several targets — that
    is exactly how identity reports a split, and an unmerge — so they are gathered
    before anything is planned. Planning each row alone would report a split as several
    independent one-target re-keys and lose the ambiguity the shape exists to preserve.

    A merge that a later unmerge in the same batch withdrew plans nothing: the absorbed
    entity is canonical again, so beliefs keyed on it are already keyed correctly. The
    unmerge's own rows cover the beliefs keyed on the survivor. Nor does a merge that
    gives an entity, or gives one to an entity, the batch has already merged and not
    yet given back: an entity yields to one survivor at a time, and only an entity that
    has not yielded can survive a merge. The identity layer ignores such a merge, and
    so does this.

    An unmerge's rows come from where the survivor stands when it happens, which is
    where the beliefs that may be about the absorbed entity have gone if the survivor
    has since been merged on. When the merge it withdraws is in the same batch, those
    beliefs have not moved yet, and the unmerge plans the beliefs keyed on the survivor
    itself. When that merge is older, they stand on the survivor's holder as the batch
    found it, which is the entity the rows name or any entity the batch merged into it
    before the unmerge, so the unmerge plans the beliefs keyed on each of them.

    ``skip_unrenderable`` drops beliefs whose ids this process cannot re-mint — a
    scheme it does not know, or a predicate it does not declare — instead of raising.
    Off by default: a belief the caller cannot re-key is a real gap in the cascade, and
    silence about it is the failure mode the whole seam exists to prevent.
    """
    batch = _landed(_group(rekeys))
    plans: list[BeliefRekey] = []
    for key, targets in batch.decisions.items():
        if key in batch.withdrawn:
            continue
        seq, kind, from_entity = key
        for holder in batch.holders.get(key, (from_entity,)):
            # A holder other than the row's own entity stays where it is, or goes
            # wherever the batch takes it, rather than onto the row's entity.
            stays = [holder if target == from_entity else target for target in targets]
            for row in index.beliefs_for(holder):
                settled = _settle(kind, holder, stays, seq, batch)
                candidates = _successors(row.belief_id, holder, settled, predicates)
                if candidates is None:
                    if skip_unrenderable:
                        continue
                    raise UnrenderableBelief(
                        f"belief {row.belief_id!r} names {holder} but cannot be re-minted "
                        f"by this registry; pass skip_unrenderable=True to drop it knowingly"
                    ) from _why_unrenderable(row.belief_id, predicates)
                plans.append(
                    BeliefRekey(
                        seq=seq,
                        kind=kind,
                        belief_id=row.belief_id,
                        from_entity=holder,
                        candidates=candidates,
                    )
                )
    return plans


def _group(rekeys: Iterable[RekeyRow]) -> _Grouped:
    """Gather the rows of one identity decision, keeping identity's own order."""
    grouped: _Grouped = {}
    for row in rekeys:
        grouped.setdefault((row.seq, row.kind, row.from_entity), []).append(row.to_entity)
    return grouped


def _landed(grouped: _Grouped) -> _Batch:
    """
    Drop the decisions that change nothing, walking the batch in order.

    A merge that names, on either side, an entity an earlier merge in the batch
    absorbed, with no unmerge giving it back in between, is ignored, as is an unmerge
    of a pair the batch has left apart. An entity the batch has not touched yet is
    taken as the rows say: it was free to merge or to survive one, or merged into the
    unmerge's survivor, since identity wrote the row.

    An unmerge's rows name where the survivor stands at that point, so one that
    withdraws a merge in the batch lands when the batch's own merges lead that merge's
    survivor to the entity the rows name, or when the rows name the survivor itself.
    """
    merged_into: dict[UUID, tuple[UUID, _Key] | None] = {}
    entities = dict.fromkeys(
        entity
        for key in sorted(grouped, key=lambda key: key[0])
        for entity in (key[2], *grouped[key])
    )

    def standing(entity: UUID) -> UUID:
        seen = {entity}
        while (link := merged_into.get(entity)) is not None and link[0] not in seen:
            entity = link[0]
            seen.add(entity)
        return entity

    ignored: set[_Key] = set()
    withdrawn: set[_Key] = set()
    holders: dict[_Key, tuple[UUID, ...]] = {}
    for key in sorted(grouped, key=lambda key: key[0]):
        _seq, kind, from_entity = key
        targets = grouped[key]
        if kind == "merge":
            survivor = targets[0]
            if merged_into.get(from_entity) is not None or merged_into.get(survivor) is not None:
                ignored.add(key)
            else:
                merged_into[from_entity] = (survivor, key)
        elif kind == "unmerge":
            held: list[UUID] = []
            for absorbed in (target for target in targets if target != from_entity):
                if absorbed not in merged_into:
                    # Merged before the batch: what may be about it stands wherever the
                    # survivor's beliefs stood as the batch found them.
                    merged_into[absorbed] = None
                    held.extend(
                        entity
                        for entity in (from_entity, *entities)
                        if standing(entity) == from_entity and entity not in held
                    )
                    continue
                link = merged_into[absorbed]
                if link is None or from_entity not in {link[0], standing(link[0])}:
                    ignored.add(key)
                    continue
                withdrawn.add(link[1])
                merged_into[absorbed] = None
                if link[0] not in held:
                    held.append(link[0])
            holders[key] = tuple(held)
    return _Batch(
        decisions={key: targets for key, targets in grouped.items() if key not in ignored},
        withdrawn=frozenset(withdrawn),
        holders=holders,
    )


def _settle(
    kind: str, from_entity: UUID, targets: Sequence[UUID], seq: int, batch: _Batch
) -> list[UUID]:
    """
    Follow each target through any later decision in the same batch.

    A batch can chain: ``A`` is absorbed into ``B``, then ``B`` into ``C``. Planning
    each row against the index alone stops at ``B`` — nothing was ever minted on ``B``,
    so no row plans it onward — and the caller re-keys onto an id the same batch has
    already superseded. Only the beliefs the *index* knows about are planned, so the
    composition has to happen here rather than by re-reading the index.

    A split's products are followed the same way, independently: one may be absorbed
    later while its siblings are not, and each candidate answers for itself.

    An unmerge's candidates are followed through later unmerges too. If ``C`` gives
    back ``B``, and later in the batch ``B`` gives back ``A``, a belief on ``C`` that
    was ``B``'s may have been ``A``'s all along: nothing of ``A``'s has moved off ``C``
    yet, because the second unmerge's rows name ``B``, which holds nothing in the
    index. So ``A`` is offered as well. That adds a candidate rather than choosing
    one. A merge's or a split's targets are not followed this way: the belief was
    keyed on an entity the batch knows, and what an unmerge gives back was never on it.

    An unmerge starts from an entity that is still there, so arriving back at it is
    not a ring: the id it gave back was absorbed into it again later in the batch.
    Targets that settle on the same id are one candidate.
    """
    settled: list[UUID] = []
    branching = kind == "unmerge"
    for target in targets:
        seen = {target} if branching else {from_entity, target}
        for end in _follow(target, seq, seen, from_entity, batch, branching=branching):
            if end not in settled:
                settled.append(end)
    return settled


def _follow(
    entity: UUID,
    at: int,
    seen: set[UUID],
    origin: UUID,
    batch: _Batch,
    *,
    branching: bool,
) -> list[UUID]:
    """Where ``entity`` ends up after position ``at`` in the batch, in time order."""
    current = entity
    while (step := _next(current, at, batch, branching=branching)) is not None:
        when, kind, onward = step
        if kind == "unmerge":
            # Each side starts afresh: an unmerge is not a step of any ring.
            ends = list(_branch(current, when, origin, batch))
            for end in _branch(onward, when, origin, batch):
                if end not in ends:
                    ends.append(end)
            return ends
        if onward in seen:
            raise MergeCycleInBatch(
                f"re-key rows lead {origin} back to {onward}; a ring has no "
                f"last element, so there is no successor to re-mint to"
            )
        seen.add(onward)
        current = onward
        at = max(at, when)
    return [current]


def _branch(entity: UUID, at: int, origin: UUID, batch: _Batch) -> tuple[UUID, ...]:
    """One side of an unmerge at ``at``, followed from ``entity``; walked once per plan."""
    if (ends := batch.branches.get((entity, at))) is None:
        # A ring raises before anything is kept, so ``origin`` names it on every walk.
        ends = tuple(_follow(entity, at, {entity}, origin, batch, branching=True))
        batch.branches[entity, at] = ends
    return ends


def _next(entity: UUID, at: int, batch: _Batch, *, branching: bool) -> _Step | None:
    """
    The next decision in the batch that moves ``entity`` on from position ``at``.

    A merge of ``entity`` is followed wherever it falls: one before ``at`` means the
    entity had already yielded. A split does not supersede its original — the original
    survives it — so an entity that is later split has not moved, and following one of
    its products would be this layer choosing among them, which is the judgment it may
    not make. A merge that a later unmerge in the batch withdrew is not followed either:
    by the end of the batch the entity yields to nothing through it.

    With ``branching``, an unmerge after ``at`` from where ``entity`` stands is
    followed too, as ``(seq, "unmerge", the entity it gave back)``.
    """
    key = (entity, at, branching)
    if key not in batch.steps:
        batch.steps[key] = _scan(entity, at, batch, branching=branching)
    return batch.steps[key]


def _scan(entity: UUID, at: int, batch: _Batch, *, branching: bool) -> _Step | None:
    """``_next`` worked out from the batch's decisions."""
    found: _Step | None = None
    for key, targets in batch.decisions.items():
        seq, kind, from_entity = key
        if kind == "merge" and from_entity == entity and key not in batch.withdrawn:
            if seq <= at:
                return (seq, kind, targets[0])
            step = (seq, kind, targets[0])
        elif (
            branching
            and kind == "unmerge"
            and seq > at
            and entity in {from_entity, *batch.holders.get(key, ())}
        ):
            step = (seq, kind, next(t for t in targets if t != from_entity))
        else:
            continue
        if found is None or step[0] < found[0]:
            found = step
    return found


def _why_unrenderable(belief_id: str, predicates: PredicateRegistry) -> BeliefError:
    """The error the re-mint attempt actually hit, kept as the cause."""
    try:
        predicates.render(predicates.parse(belief_id))
    except BeliefError as exc:
        return exc
    return UnrenderableBelief(f"belief {belief_id!r} renders on its own but not re-keyed")


def _successors(
    belief_id: str,
    from_entity: UUID,
    targets: Sequence[UUID],
    predicates: PredicateRegistry,
) -> tuple[str, ...] | None:
    """The ids ``belief_id`` becomes under each target, or ``None`` if unrenderable."""
    try:
        minted = predicates.parse(belief_id)
        return tuple(
            predicates.render(
                MintedId(
                    scheme=minted.scheme,
                    namespace=minted.namespace,
                    name=minted.name,
                    entity_ids=tuple(
                        target if entity == from_entity else entity for entity in minted.entity_ids
                    ),
                )
            )
            for target in targets
        )
    except BeliefError:
        # A malformed id, an unknown scheme, an undeclared predicate: every one means
        # this registry cannot reproduce the id, which is the caller's answer. A
        # non-BeliefError is a bug here and is left to propagate.
        return None
