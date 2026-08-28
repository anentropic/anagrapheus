"""
The belief index as a fold over the minting events.

No new dispatch machinery: :class:`BeliefIndexFold` implements the timeline's existing
``ProjectionFold`` protocol and is driven by the existing ``Projector``, exactly as the
identity fold is. Events the vocabulary does not recognise are skipped — this fold
shares the stream with the application's whole domain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anagrapheus.beliefs.vocabulary import BeliefVocabulary
from anagrapheus.timeline.registry import EventKey

if TYPE_CHECKING:
    from anagrapheus.beliefs.store import BeliefIndexStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.registry import EventTypeRegistry

__all__ = ["FOLD_NAME", "BeliefIndexFold"]

FOLD_NAME = "anagrapheus.beliefs"


class BeliefIndexFold:
    """Folds minted belief ids into the index."""

    def __init__(
        self,
        store: BeliefIndexStore,
        registry: EventTypeRegistry,
        *,
        vocabulary: BeliefVocabulary | None = None,
        name: str = FOLD_NAME,
    ) -> None:
        self._store = store
        self._registry = registry
        self._vocabulary = vocabulary if vocabulary is not None else BeliefVocabulary.default()
        self._name = name

    @property
    def name(self) -> str:
        """The position key."""
        return self._name

    def apply(self, event: StoredEvent, txn: object) -> None:
        """Fold one event, if the vocabulary recognises it as a minting."""
        envelope = event.envelope
        role = self._vocabulary.role_for(envelope.namespace, envelope.type)
        if role is None:
            return
        key = EventKey(envelope.namespace, envelope.type, envelope.schema_version)
        minted = self._vocabulary.adapt(role, self._registry.decode(key, event.payload))
        self._store.minted(minted, event.seq)
