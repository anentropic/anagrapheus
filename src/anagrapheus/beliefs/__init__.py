"""
Belief ids, and the index that makes identity's re-key seam executable.

doxastica keeps the beliefs: append-only revision chains, what rests on what, what a
retraction left stale, all held per scope so several holders can believe contradictory
things at once. It deliberately does not decide what a belief is *about* —
``belief_id`` is a caller-supplied string — and choosing those ids well is the whole
integration cost. This subpackage pays it:

- **A predicate registry in code**, which is the only way to mint. Predicates are
  schema: declared once, identical in every process that mints or reads, failing at
  import rather than at runtime.
- **The id names the question, the value answers it.** Declared arity closes the
  functional/relational trap mechanically — a predicate declared ``knows/2`` cannot be
  minted at arity 1, so revising it cannot silently retract a claim about a third
  party nobody mentioned. A functional predicate may also declare the words its value
  can be, so two spellings of one answer cannot become two answers.
- **Beliefs are a fold over the log.** An event that changes what somebody believes is
  read as revise / expand / contract operations, and :class:`BeliefFold` applies them
  to doxastica, so the belief store can be dropped and rebuilt like every other
  projection. The library's own :class:`BeliefRevised` and its siblings cover beliefs no
  domain event carries; an application binds its own events to an interpreter.
- **An index from entity to belief id**, written by the same fold. This is the piece
  neither library has: doxastica can filter *by* a belief id but never look inside
  one, and the identity layer knows entities and has never heard of a belief. Without
  it, "a merge returns exactly which beliefs need re-keying" is a promise nothing can
  keep.

The re-key plan is computed here and applied by the caller. Re-keying is an AGM
operation with real semantics — what happens to the old id, what happens when the new
one is already held — and those are judgments about belief, not about ids.
"""

from anagrapheus.beliefs.divergence import Disagreement, divergence
from anagrapheus.beliefs.errors import (
    BeliefError,
    MalformedBeliefId,
    MergeCycleInBatch,
    OutsideVocabulary,
    UnknownPredicate,
    UnknownScheme,
    UnrenderableBelief,
    UnresolvedStateRef,
    WrongArity,
)
from anagrapheus.beliefs.events import (
    BUILTIN_INTERPRETERS,
    BeliefContracted,
    BeliefExpanded,
    BeliefRevised,
    EdgeRecord,
    register_builtin_events,
    stance_name,
)
from anagrapheus.beliefs.fold import FOLD_NAME, BeliefFold, check
from anagrapheus.beliefs.index import BeliefIndex
from anagrapheus.beliefs.ops import (
    BeliefOp,
    Contract,
    Edge,
    Expand,
    Interpreter,
    Revise,
    StateRef,
)
from anagrapheus.beliefs.predicates import (
    CURRENT_SCHEME,
    MintedId,
    Predicate,
    PredicateKind,
    PredicateRegistry,
)
from anagrapheus.beliefs.rekey import BeliefRekey, plan_rekeys
from anagrapheus.beliefs.sqlite import SQLiteBeliefIndexStore
from anagrapheus.beliefs.store import BeliefIndexStore, BeliefRow
from anagrapheus.beliefs.writer import BeliefEvents, RelationalValue

__all__ = [
    "BUILTIN_INTERPRETERS",
    "CURRENT_SCHEME",
    "FOLD_NAME",
    "BeliefContracted",
    "BeliefError",
    "BeliefEvents",
    "BeliefExpanded",
    "BeliefFold",
    "BeliefIndex",
    "BeliefIndexStore",
    "BeliefOp",
    "BeliefRekey",
    "BeliefRevised",
    "BeliefRow",
    "Contract",
    "Disagreement",
    "Edge",
    "EdgeRecord",
    "Expand",
    "Interpreter",
    "MalformedBeliefId",
    "MergeCycleInBatch",
    "MintedId",
    "OutsideVocabulary",
    "Predicate",
    "PredicateKind",
    "PredicateRegistry",
    "RelationalValue",
    "Revise",
    "SQLiteBeliefIndexStore",
    "StateRef",
    "UnknownPredicate",
    "UnknownScheme",
    "UnrenderableBelief",
    "UnresolvedStateRef",
    "WrongArity",
    "check",
    "divergence",
    "plan_rekeys",
    "register_builtin_events",
    "stance_name",
]
