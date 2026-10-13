"""
The identity registry as a fold over the decisions.

Nothing here is new dispatch machinery: :class:`IdentityFold` implements the
timeline's existing ``ProjectionFold`` protocol and is driven by the existing
``Projector``. That is the point — identity is a projection like any other, which is
why a bad merge is corrected by appending an event and re-folding rather than by
editing a table.

Events the vocabulary does not recognise are skipped. The identity fold shares the
stream with the application's whole domain, so most of what it sees is not its
business.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anagrapheus.identity.events import Aliased, Established, Merged, Split, Unmerged
from anagrapheus.identity.vocabulary import IdentityRole, IdentityVocabulary
from anagrapheus.timeline.registry import EventKey

if TYPE_CHECKING:
    from anagrapheus.identity.store import IdentityStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.folds import FoldDeterminism
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["FOLD_NAME", "IdentityFold"]

FOLD_NAME = "anagrapheus.identity"


class IdentityFold:
    """Folds identity decisions into the registry's state."""

    def __init__(
        self,
        store: IdentityStore,
        registry: EventTypeRegistry,
        *,
        vocabulary: IdentityVocabulary | None = None,
        name: str = FOLD_NAME,
    ) -> None:
        self._store = store
        self._registry = registry
        self._vocabulary = vocabulary if vocabulary is not None else IdentityVocabulary.default()
        self._name = name

    @property
    def name(self) -> str:
        """The position key."""
        return self._name

    #: Deterministic: a decode-and-index fold, with no draw anywhere in it.
    fold: FoldDeterminism = "deterministic"

    def apply(self, event: StoredEvent, txn: object) -> None:
        """Fold one event, if the vocabulary recognises it as an identity decision."""
        envelope = event.envelope
        role = self._vocabulary.role_for(envelope.namespace, envelope.type)
        if role is None:
            return
        key = EventKey(envelope.namespace, envelope.type, envelope.schema_version)
        decision = self._vocabulary.adapt(role, self._registry.decode(key, event.payload))
        match role, decision:
            case IdentityRole.ESTABLISHED, Established():
                self._store.established(decision, event.seq)
            case IdentityRole.ALIASED, Aliased():
                self._store.aliased(decision, event.seq)
            case IdentityRole.MERGED, Merged():
                self._store.merged(decision, event.seq)
            case IdentityRole.SPLIT, Split():
                self._store.split(decision, event.seq)
            case IdentityRole.UNMERGED, Unmerged():
                # The store records an unmerge that matches no current merge as a
                # no-op rather than raising: it is canon, and replay would hit it again.
                self._store.unmerged(decision, event.seq)
            case _:
                raise TypeError(
                    f"vocabulary adapted {envelope.type!r} for role {role.value!r} "
                    f"into {type(decision).__name__}"
                )
