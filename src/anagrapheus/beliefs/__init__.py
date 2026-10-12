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
- **An index from entity to belief id**, folded from the log. This is the piece
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
    WrongArity,
)
from anagrapheus.beliefs.events import BeliefMinted
from anagrapheus.beliefs.fold import FOLD_NAME, BeliefIndexFold
from anagrapheus.beliefs.index import BeliefIndex
from anagrapheus.beliefs.minting import mint
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
from anagrapheus.beliefs.vocabulary import (
    BeliefRole,
    BeliefVocabulary,
    RoleBinding,
    register_builtin_events,
)
from anagrapheus.beliefs.writer import BeliefWriter, RelationalValue

__all__ = [
    "CURRENT_SCHEME",
    "FOLD_NAME",
    "BeliefError",
    "BeliefIndex",
    "BeliefIndexFold",
    "BeliefIndexStore",
    "BeliefMinted",
    "BeliefRekey",
    "BeliefRole",
    "BeliefRow",
    "BeliefVocabulary",
    "BeliefWriter",
    "Disagreement",
    "MalformedBeliefId",
    "MergeCycleInBatch",
    "MintedId",
    "OutsideVocabulary",
    "Predicate",
    "PredicateKind",
    "PredicateRegistry",
    "RelationalValue",
    "RoleBinding",
    "SQLiteBeliefIndexStore",
    "UnknownPredicate",
    "UnknownScheme",
    "UnrenderableBelief",
    "WrongArity",
    "divergence",
    "mint",
    "plan_rekeys",
    "register_builtin_events",
]
