"""
Canonical referents, and the record of the decisions that made them canonical.

Belief ids and pin keys both presuppose stable referents; this is where they come
from. Two paths, and which applies depends entirely on whether you have naming
authority:

- **Establish** — the system declares an entity into existence and mints its id.
  There is no external referent to match, so resolution is meaningless here. Most of
  what a long-running agent reasons about is this case: tasks, runs, hypotheses,
  plans, findings.
- **Resolve** — the entity already exists independently and the system must discover
  whether ``alice@corp``, ``Alice Smith`` and ``A. Smith`` are one referent or three.
  The world already decided; the agent is guessing.

For the second population the decisions themselves become events, and the registry is
a fold over them — rebuildable, disposable, replayable, auditable. A bad merge is
undone by appending an ``Unmerged`` that withdraws it (see :func:`unmerge`), not by
mutating a table. What that buys is governance, not inference: something still has to
judge that two forms name one thing, and nothing here will do it for you.

Merge is the joint with the belief layer: it means every belief keyed on the absorbed
entity is now suspect, and :meth:`IdentityRegistry.rekeys_since` is how that
consequence leaves this layer.
"""

from anagrapheus.identity.decisions import (
    ENTITY_PIN_KIND,
    alias,
    establish,
    merge,
    mint_entity_id,
    split,
    unmerge,
)
from anagrapheus.identity.errors import (
    AlreadyMerged,
    AmbiguousSurfaceForm,
    IdentityError,
    MergeCycle,
    NotMerged,
    UnknownEntity,
)
from anagrapheus.identity.events import (
    Aliased,
    Established,
    Merged,
    Split,
    SplitAssignment,
    Unmerged,
    normalize_surface_form,
)
from anagrapheus.identity.fold import FOLD_NAME, IdentityFold
from anagrapheus.identity.ladder import UNGRADED, Ladder
from anagrapheus.identity.registry import Candidate, IdentityRegistry, Resolution
from anagrapheus.identity.sqlite import SQLiteIdentityStore
from anagrapheus.identity.store import AliasRow, DecisionRow, EntityRow, IdentityStore, RekeyRow
from anagrapheus.identity.vocabulary import (
    IdentityRole,
    IdentityVocabulary,
    RoleBinding,
    register_builtin_events,
)

__all__ = [
    "AlreadyMerged",
    "ENTITY_PIN_KIND",
    "FOLD_NAME",
    "UNGRADED",
    "AliasRow",
    "Aliased",
    "AmbiguousSurfaceForm",
    "Candidate",
    "DecisionRow",
    "EntityRow",
    "Established",
    "IdentityError",
    "IdentityFold",
    "IdentityRegistry",
    "IdentityRole",
    "IdentityStore",
    "IdentityVocabulary",
    "Ladder",
    "MergeCycle",
    "Merged",
    "NotMerged",
    "RekeyRow",
    "Resolution",
    "RoleBinding",
    "SQLiteIdentityStore",
    "Split",
    "SplitAssignment",
    "UnknownEntity",
    "Unmerged",
    "alias",
    "establish",
    "merge",
    "mint_entity_id",
    "normalize_surface_form",
    "register_builtin_events",
    "split",
    "unmerge",
]
