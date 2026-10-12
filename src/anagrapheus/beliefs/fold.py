"""
Beliefs as a fold over the log: doxastica and the entity index, both rebuilt from it.

Doc 00 §2: the log is the authority and the belief store is one of its projections.
:class:`BeliefFold` is the projection. Every event an interpreter recognises is read as
a few :mod:`~anagrapheus.beliefs.ops`, and each one is checked, minted, written into
doxastica and recorded in the entity index, in that order. Drop both and replay the log,
and the same beliefs come back — under new ``state_id`` handles, which doxastica mints,
and identical in everything else.

**Three things a fold over an external store has to get right**, and how:

- **Idempotence.** :class:`~anagrapheus.timeline.folds.Projector` commits a fold's
  position in the store's transaction, and doxastica is not in it, so a crash between
  the two re-applies the last batch. doxastica's ``revise`` is not idempotent, so an
  operation whose scope already holds a state carrying this event's id for this belief
  reuses that state instead of writing another, and lays its edges again (``add_edge``
  is idempotent), since a crash can fall between the state and its edges. Recognising
  the state is exact, because a state's ``source_event_id`` is always the event it was
  folded from (see :mod:`~anagrapheus.beliefs.ops`) and one event may write a
  proposition in a scope at most once.
- **Determinism.** The fold mints through the registry it holds and the interpreter is
  required to be pure. Edges name their target by :class:`~anagrapheus.beliefs.ops.StateRef`,
  which resolves to the same state on every fold; a ``state_id`` would not.
- **Reset.** doxastica has no "clear". :meth:`BeliefFold.reset` takes the empty store to
  fold into — a fresh ``InMemoryBackend``, a new Ladybug database — and empties the
  index beside it. Whoever reads beliefs must then read the new store, which is why the
  caller owns that swap.

**A refusal here is a stuck projection, not a rejected write.** By the time this fold
sees an event the event is canon, and an operation that fails a check fails on every
replay. So the same checks run *before* append — :func:`check`, which
:class:`~anagrapheus.beliefs.writer.BeliefEvents` calls for the library's own events and
an application should call for its own — and the fold raises rather than skipping, so a
bad event is loud rather than a belief silently missing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from anagrapheus.beliefs.errors import UnresolvedStateRef
from anagrapheus.beliefs.events import BUILTIN_INTERPRETERS
from anagrapheus.beliefs.ops import Contract, Revise
from anagrapheus.beliefs.store import BeliefRow
from anagrapheus.timeline.registry import EventKey

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from doxastica import BeliefState, BeliefStore

    from anagrapheus.beliefs.ops import BeliefOp, Interpreter, StateRef
    from anagrapheus.beliefs.predicates import PredicateRegistry
    from anagrapheus.beliefs.store import BeliefIndexStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.folds import FoldDeterminism
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["FOLD_NAME", "BeliefFold", "check"]

_log = logging.getLogger(__name__)

FOLD_NAME = "anagrapheus.beliefs"


def check(predicates: PredicateRegistry, ops: Sequence[BeliefOp]) -> None:
    """
    Raise if the fold would refuse ``ops``. Call it before appending the event they came from.

    The predicate is declared, the arity is right (:class:`~anagrapheus.beliefs.errors.WrongArity`),
    the value is one the predicate can hold (a bool for a relational one, a declared word
    for one with a vocabulary), and no proposition is written twice in one scope by one
    event — which would make the second write indistinguishable from a replay of the
    first.
    """
    seen: set[tuple[str, str]] = set()
    for op in ops:
        belief_id = predicates.belief_id(op.predicate, *op.entity_ids)
        if not isinstance(op, Contract):
            predicates.predicate(op.predicate).check_value(op.value)
        key = (op.scope_id, belief_id)
        if key in seen:
            raise ValueError(
                f"one event writes {belief_id!r} in {op.scope_id!r} twice; a belief's "
                f"source is the event it was folded from, so the second write could not be "
                f"told apart from a replay of the first"
            )
        seen.add(key)


class BeliefFold:
    """
    Folds belief-bearing events into a doxastica store and the entity index.

    ``interpret`` maps ``(namespace, type)`` to an
    :data:`~anagrapheus.beliefs.ops.Interpreter`, added to (and able to replace) the
    library's own :data:`~anagrapheus.beliefs.events.BUILTIN_INTERPRETERS`. Events no
    interpreter claims are skipped without being decoded — this fold shares the stream
    with the application's whole domain.
    """

    def __init__(
        self,
        store: BeliefStore,
        predicates: PredicateRegistry,
        index: BeliefIndexStore,
        types: EventTypeRegistry,
        *,
        interpret: Mapping[tuple[str, str], Interpreter] | None = None,
        name: str = FOLD_NAME,
    ) -> None:
        self._store = store
        self._predicates = predicates
        self._index = index
        self._types = types
        self._interpret = {**BUILTIN_INTERPRETERS, **(interpret or {})}
        self._name = name

    @property
    def name(self) -> str:
        """The position key."""
        return self._name

    #: Deterministic: minting is derivation and interpreters are pure, so the same log
    #: folds to the same beliefs.
    fold: FoldDeterminism = "deterministic"

    @property
    def store(self) -> BeliefStore:
        """The doxastica store this fold currently writes into."""
        return self._store

    def reset(self, store: BeliefStore) -> None:
        """
        Start over into ``store``, which must be empty, and empty the index.

        Pass it as :meth:`~anagrapheus.timeline.folds.Projector.rebuild`'s ``reset``,
        with ``reset_in_transaction=True`` when the index shares the log's database.
        """
        self._store = store
        self._index.reset()

    def apply(self, event: StoredEvent, txn: object) -> None:
        """Fold one event, if an interpreter claims its type."""
        envelope = event.envelope
        interpreter = self._interpret.get((envelope.namespace, envelope.type))
        if interpreter is None:
            return
        key = EventKey(envelope.namespace, envelope.type, envelope.schema_version)
        ops = interpreter(envelope, self._types.decode(key, event.payload))
        check(self._predicates, ops)
        for op in ops:
            self._apply(op, envelope.event_id, event.seq)

    def _apply(self, op: BeliefOp, event_id: UUID, seq: int) -> None:
        minted = self._predicates.mint(op.predicate, *op.entity_ids)
        belief_id = self._predicates.render(minted)
        self._index.minted(BeliefRow(belief_id, op.predicate, minted.entity_ids, seq))
        if isinstance(op, Contract):
            if self._written(op.scope_id, belief_id, event_id) is None:
                self._store.contract(op.scope_id, belief_id, event_id)
                _log.debug("contract %s: %s", op.scope_id, belief_id)
            return
        # Every target resolves before anything is written, so an edge the log cannot
        # satisfy refuses the operation whole rather than leaving its state half-laid.
        targets = [(self._resolve(edge.to), edge.type) for edge in op.edges]
        state = self._written(op.scope_id, belief_id, event_id)
        if state is not None:
            # A replay of a batch that was cut short: the state may be here and its
            # edges not, so they are laid again below. `add_edge` is idempotent.
            _log.debug("reuse %s in %s: already folded from %s", belief_id, op.scope_id, event_id)
        elif isinstance(op, Revise):
            state = self._store.revise(op.scope_id, belief_id, op.value, event_id, op.stance)
        else:
            state = self._store.expand(op.scope_id, belief_id, op.value, event_id, op.stance)
        _log.debug("%s %s: %s = %r", type(op).__name__.lower(), op.scope_id, belief_id, op.value)
        for target, edge_type in targets:
            self._store.add_edge(state.state_id, target.state_id, edge_type)

    def state(self, ref: StateRef) -> BeliefState | None:
        """
        The state ``ref`` names, or ``None`` if this fold has not written it.

        How a caller that appended a belief event gets the state it produced: fold, then
        look it up by the event it appended.
        """
        return self._written(ref.scope_id, ref.belief_id, ref.event_id)

    def _written(self, scope_id: str, belief_id: str, event_id: UUID) -> BeliefState | None:
        """The state this event already wrote for this belief in this scope, if any."""
        return next(
            (
                state
                for state in self._store.get_revision_chain(belief_id)
                if state.scope_id == scope_id and state.source_event_id == event_id
            ),
            None,
        )

    def _resolve(self, ref: StateRef) -> BeliefState:
        state = self._written(ref.scope_id, ref.belief_id, ref.event_id)
        if state is None:
            # doxastica's `add_edge` no-ops on a missing endpoint, by design. Here that
            # would be a dependency the log records and the store silently lacks, so a
            # retraction would no longer report what it compromised.
            raise UnresolvedStateRef(
                f"no state for {ref.belief_id!r} in {ref.scope_id!r} folded from "
                f"{ref.event_id}; an edge can only point at a belief the log already holds"
            )
        return state
