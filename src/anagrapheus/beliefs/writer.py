"""
Writing beliefs through the registry, and never around it.

doxastica's seam takes a ``belief_id: str``, which means any string will do and the
minting discipline is honour-based. Doc 00 §6c's divergence query works *only* if both
scopes minted through the same registry, and it names that as "the argument for making
the registry the only way to mint" — so here is the surface that makes it so. A caller
holding a :class:`BeliefWriter` never types a belief id.

It also binds the two halves of the stack that doc 00 leaves adjacent but unjoined:
doxastica's ``source_event_id`` is a caller-supplied opaque handle, and the timeline's
envelope id is exactly the handle worth supplying. Passing it makes "which event caused
this belief" answerable, and — because doxastica orders states by ``source_event_id``
in UUID7 byte order — makes its as-of cut agree with the log's own order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from doxastica import Stance

from anagrapheus.beliefs.minting import mint
from anagrapheus.beliefs.predicates import PredicateKind

if TYPE_CHECKING:
    from uuid import UUID

    from doxastica import BeliefState, BeliefStore

    from anagrapheus.beliefs.predicates import PredicateRegistry
    from anagrapheus.timeline.envelope import PendingEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["BeliefWriter", "RelationalValue", "Written"]

_log = logging.getLogger(__name__)

#: What a relational belief's value may be: whether the proposition holds, and nothing
#: else. *How firmly* is a separate axis, and doxastica already has a parameter for it —
#: putting a grade in the value would merge the two, which is the merge R21 forbids and
#: which doxastica's own encoding refuses anyway (values are JSON; ``Stance`` is not).
type RelationalValue = bool


class Written(NamedTuple):
    """
    A belief written, and the event recording that its id was minted.

    The event is returned rather than appended for the reason every helper in this
    library returns one: a belief is written *because* something happened, and the
    caller owns that batch and its transaction. But it is returned rather than left
    implicit because the alternative was a trap — see :class:`BeliefWriter`.
    """

    state: BeliefState
    minted: PendingEvent


class BeliefWriter:
    """
    Writes beliefs into a ``BeliefStore``, keyed by predicate rather than by id.

    A thin wrapper by design: it adds minting, the ``source_event_id`` binding, and
    the value checks the predicate declares. Everything else — revision semantics,
    scopes, retraction cascades — is doxastica's, reached through the store the caller
    passes in.

    **Writing hands back an event you must append.** An earlier draft minted the id and
    said nothing, which made this class a trap: a caller who used only the writer — the
    surface documented as the way to write beliefs — got no index entries at all, so
    :func:`~anagrapheus.beliefs.rekey.plan_rekeys` found nothing and a merge silently
    re-keyed none of their beliefs. The layer's own front door opened exactly the hole
    it exists to close. Returning the event makes the recording impossible to forget
    without discarding a return value.
    """

    def __init__(
        self,
        store: BeliefStore,
        predicates: PredicateRegistry,
        types: EventTypeRegistry,
        *,
        scope_id: str,
    ) -> None:
        self._store = store
        self._predicates = predicates
        self._types = types
        self._scope_id = scope_id

    @property
    def scope_id(self) -> str:
        """The scope this writer writes into."""
        return self._scope_id

    def for_scope(self, scope_id: str) -> BeliefWriter:
        """Another writer over the same store and registry, in a different scope."""
        return BeliefWriter(self._store, self._predicates, self._types, scope_id=scope_id)

    def revise(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        source_event_id: UUID,
        stance: Stance = Stance.certain,
        *,
        correlation_id: UUID | None = None,
    ) -> Written:
        """AGM revision: supersede whatever this scope held for this proposition."""
        belief_id, minted = self._mint(predicate, entity_ids, value, correlation_id)
        state = self._store.revise(self._scope_id, belief_id, value, source_event_id, stance)
        _log.debug("revise %s: %s = %r", self._scope_id, belief_id, value)
        return Written(state, minted)

    def expand(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        source_event_id: UUID,
        stance: Stance = Stance.certain,
        *,
        correlation_id: UUID | None = None,
    ) -> Written:
        """AGM expansion: add without consistency enforcement."""
        belief_id, minted = self._mint(predicate, entity_ids, value, correlation_id)
        state = self._store.expand(self._scope_id, belief_id, value, source_event_id, stance)
        _log.debug("expand %s: %s = %r", self._scope_id, belief_id, value)
        return Written(state, minted)

    def contract(self, predicate: str, entity_ids: tuple[UUID, ...], source_event_id: UUID) -> None:
        """AGM contraction: retract this proposition in this scope."""
        belief_id = self._predicates.belief_id(predicate, *entity_ids)
        self._store.contract(self._scope_id, belief_id, source_event_id)
        _log.debug("contract %s: %s", self._scope_id, belief_id)

    def _mint(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        correlation_id: UUID | None,
    ) -> tuple[str, PendingEvent]:
        """Check the value, then mint through the recorded path rather than around it."""
        self._check(predicate, value)
        return mint(
            self._types,
            self._predicates,
            predicate,
            *entity_ids,
            correlation_id=correlation_id,
        )

    def _check(self, predicate: str, value: Any) -> None:
        """Refuse the relational shape doc 00 §6a warns about, and undeclared answers."""
        declared = self._predicates.predicate(predicate)
        declared.check_value(value)
        if declared.kind is PredicateKind.RELATIONAL and not isinstance(value, bool):
            # The arity check already stops `knows(alice)` from being minted. This
            # catches the same mistake arriving the other way: a relational predicate
            # minted at full arity but carrying a substantive value is a proposition
            # smuggled into the answer, and revising it would retract a claim about an
            # entity nobody mentioned.
            #
            # A `Stance` is refused here too, though it looks like it belongs: how
            # firmly a belief is held is doxastica's own `stance` argument, one axis
            # over. Accepting it as a value would merge ranking with epistemic state.
            hint = (
                " (how firmly it is held is the `stance` argument, not the value)"
                if isinstance(value, Stance)
                else ""
            )
            raise TypeError(
                f"predicate {predicate!r} is relational, so its value asserts the "
                f"proposition rather than answering it: pass a bool, not "
                f"{type(value).__name__}{hint}"
            )
