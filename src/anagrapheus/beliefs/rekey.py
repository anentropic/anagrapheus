"""
The other side of identity's re-key seam.

Doc 00 §5d gives the payoff: a merge means every belief keyed on the absorbed entity
is now suspect, and the belief layer turns that into a list of states to re-key. The
identity layer states the consequence as ``(kind, from_entity, to_entity)`` rows and
performs no cascade — it does not know what a belief is. This module knows what a
belief *id* is, which is enough to say which ones are affected and what they would
become.

**It plans; it does not apply.** Re-keying a belief is an AGM operation with real
semantics: whether the old id is contracted or left standing, what happens when the
new id already carries a belief, whose stance survives a collision. Those are
judgments about belief, and this library owns the id and not the belief. The plan is
handed to the caller with everything computed and nothing decided.
"""

from __future__ import annotations

from dataclasses import dataclass
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

#: Rows gathered by the decision that emitted them: ``(seq, kind, from_entity)``.
type _Grouped = dict[tuple[int, str, UUID], list[UUID]]


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
    - after an **unmerge**, two — the kept entity's own id and the id it gave back. A
      belief keyed on the kept entity may have been about either all along, which is
      the split's question again. One, if the same batch merges the pair again.

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
    unmerge's own rows cover the beliefs keyed on the survivor.

    ``skip_unrenderable`` drops beliefs whose ids this process cannot re-mint — a
    scheme it does not know, or a predicate it does not declare — instead of raising.
    Off by default: a belief the caller cannot re-key is a real gap in the cascade, and
    silence about it is the failure mode the whole seam exists to prevent.
    """
    grouped = _group(rekeys)
    plans: list[BeliefRekey] = []
    for (seq, kind, from_entity), targets in grouped.items():
        if kind == "merge" and _withdrawn(seq, from_entity, targets[0], grouped):
            continue
        for row in index.beliefs_for(from_entity):
            settled = _settle(kind, from_entity, targets, grouped)
            candidates = _successors(row.belief_id, from_entity, settled, predicates)
            if candidates is None:
                if skip_unrenderable:
                    continue
                raise UnrenderableBelief(
                    f"belief {row.belief_id!r} names {from_entity} but cannot be re-minted "
                    f"by this registry; pass skip_unrenderable=True to drop it knowingly"
                ) from _why_unrenderable(row.belief_id, predicates)
            plans.append(
                BeliefRekey(
                    seq=seq,
                    kind=kind,
                    belief_id=row.belief_id,
                    from_entity=from_entity,
                    candidates=candidates,
                )
            )
    return plans


def _group(rekeys: Iterable[RekeyRow]) -> dict[tuple[int, str, UUID], list[UUID]]:
    """Gather the rows of one identity decision, keeping identity's own order."""
    grouped: dict[tuple[int, str, UUID], list[UUID]] = {}
    for row in rekeys:
        grouped.setdefault((row.seq, row.kind, row.from_entity), []).append(row.to_entity)
    return grouped


def _settle(kind: str, from_entity: UUID, targets: Sequence[UUID], grouped: _Grouped) -> list[UUID]:
    """
    Follow each target through any later decision in the same batch.

    A batch can chain: ``A`` is absorbed into ``B``, then ``B`` into ``C``. Planning
    each row against the index alone stops at ``B`` — nothing was ever minted on ``B``,
    so no row plans it onward — and the caller re-keys onto an id the same batch has
    already superseded. Only the beliefs the *index* knows about are planned, so the
    composition has to happen here rather than by re-reading the index.

    A split's products are followed the same way, independently: one may be absorbed
    later while its siblings are not, and each candidate answers for itself.

    An unmerge starts from an entity that is still there, so arriving back at it is
    not a ring: the id it gave back was absorbed into it again later in the batch.
    Targets that settle on the same id are one candidate.
    """
    settled: list[UUID] = []
    for target in targets:
        current = target
        seen = {current} if kind == "unmerge" else {from_entity, current}
        while (onward := _onward(current, grouped)) is not None:
            current = onward
            if current in seen:
                raise MergeCycleInBatch(
                    f"re-key rows lead {from_entity} back to {current}; a ring has no "
                    f"last element, so there is no successor to re-mint to"
                )
            seen.add(current)
        if current not in settled:
            settled.append(current)
    return settled


def _onward(entity: UUID, grouped: _Grouped) -> UUID | None:
    """
    The single id ``entity`` yields to later in the batch, if it yields to one.

    Only a merge is followed. A split does not supersede its original — the original
    survives it — so an entity that is later split has not moved, and following one of
    its products would be this layer choosing among them, which is the judgment it may
    not make. An unmerge is not followed for the same reason, and a merge that a later
    unmerge in the batch withdrew is not followed either: by the end of the batch the
    entity yields to nothing through it.
    """
    for (seq, kind, from_entity), targets in grouped.items():
        if kind != "merge" or from_entity != entity:
            continue
        if _withdrawn(seq, entity, targets[0], grouped):
            continue
        return targets[0]
    return None


def _withdrawn(seq: int, absorbed: UUID, kept: UUID, grouped: _Grouped) -> bool:
    """Whether a later unmerge in the batch withdrew the merge of ``absorbed`` into ``kept``."""
    return any(
        kind == "unmerge" and from_entity == kept and later > seq and absorbed in targets
        for (later, kind, from_entity), targets in grouped.items()
    )


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
