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

from typing import TYPE_CHECKING, Any

from doxastica import Stance

from anagrapheus.beliefs.predicates import PredicateKind

if TYPE_CHECKING:
    from uuid import UUID

    from doxastica import BeliefState, BeliefStore

    from anagrapheus.beliefs.predicates import PredicateRegistry

__all__ = ["BeliefWriter", "RelationalValue"]

#: What a relational belief's value may be: whether the proposition holds, and nothing
#: else. *How firmly* is a separate axis, and doxastica already has a parameter for it —
#: putting a grade in the value would merge the two, which is the merge R21 forbids and
#: which doxastica's own encoding refuses anyway (values are JSON; ``Stance`` is not).
type RelationalValue = bool


class BeliefWriter:
    """
    Writes beliefs into a ``BeliefStore``, keyed by predicate rather than by id.

    A thin wrapper by design: it adds minting, the ``source_event_id`` binding, and one
    check. Everything else — revision semantics, scopes, retraction cascades — is
    doxastica's, reached through the store the caller passes in.
    """

    def __init__(
        self,
        store: BeliefStore,
        predicates: PredicateRegistry,
        *,
        scope_id: str,
    ) -> None:
        self._store = store
        self._predicates = predicates
        self._scope_id = scope_id

    @property
    def scope_id(self) -> str:
        """The scope this writer writes into."""
        return self._scope_id

    def for_scope(self, scope_id: str) -> BeliefWriter:
        """Another writer over the same store and registry, in a different scope."""
        return BeliefWriter(self._store, self._predicates, scope_id=scope_id)

    def revise(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        source_event_id: UUID,
        stance: Stance = Stance.certain,
    ) -> BeliefState:
        """AGM revision: supersede whatever this scope held for this proposition."""
        belief_id = self._belief_id(predicate, entity_ids, value)
        return self._store.revise(self._scope_id, belief_id, value, source_event_id, stance)

    def expand(
        self,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: Any,
        source_event_id: UUID,
        stance: Stance = Stance.certain,
    ) -> BeliefState:
        """AGM expansion: add without consistency enforcement."""
        belief_id = self._belief_id(predicate, entity_ids, value)
        return self._store.expand(self._scope_id, belief_id, value, source_event_id, stance)

    def contract(self, predicate: str, entity_ids: tuple[UUID, ...], source_event_id: UUID) -> None:
        """AGM contraction: retract this proposition in this scope."""
        self._store.contract(
            self._scope_id, self._predicates.belief_id(predicate, *entity_ids), source_event_id
        )

    def _belief_id(self, predicate: str, entity_ids: tuple[UUID, ...], value: Any) -> str:
        """Mint the id, and refuse the relational shape doc 00 §6a warns about."""
        declared = self._predicates.predicate(predicate)
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
        return self._predicates.belief_id(predicate, *entity_ids)
