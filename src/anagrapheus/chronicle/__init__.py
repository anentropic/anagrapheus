"""
The chronicle: compaction that is not destructive.

It keeps four things from the upstream design and discards the rest as commodity —
consolidate ranges of the log into prose that **cites the range it covers**; supersede
rather than correct; split recency from depth; return a distilled result rather than raw
chunks. This subpackage is those four, and nothing else.

**It is a projection, and the first generative one.** Derived from canon, never
authoritative, watermarked — but its ``apply`` makes a model call, so the same log
yields the same *anchors* and regenerated prose. That is a class of fold, not a special
case: fold determinism is a declared property of the fold, the fold-replay invariant
(same log, same projections) is scoped to deterministic folds, and generative folds
carry **anchor-replay** instead — an invariant of the same strength over a smaller
surface.

Three consequences the API makes structural rather than advisory:

- :class:`~anagrapheus.chronicle.reading.Anchors` and
  :class:`~anagrapheus.chronicle.reading.Prose` are separate objects, so a binding read
  cannot reach generated prose by accident.
- :meth:`~anagrapheus.timeline.folds.Projector.rebuild` refuses this fold without an
  explicit acknowledgement, because rebuilding it re-runs every model call it ever made
  and returns different words.
- :meth:`~anagrapheus.chronicle.store.ChronicleStore.export` and ``load`` exist because
  this is the one read model better **carried** across a backend swap than rebuilt.
  Carrying grants it no authority: a carried chronicle and a rebuilt one are equally
  valid, since neither is canon.
"""

# Design: note 05; upstream agent-core doc 00 §7; upstream NVM R26, R61.

from anagrapheus.chronicle.fold import (
    FOLD_NAME,
    ChronicleFold,
    Consolidation,
    EventTypes,
    Summarizer,
    UnitBoundary,
    section_id,
)
from anagrapheus.chronicle.reading import (
    Anchors,
    Brief,
    Prose,
    Recent,
    Retriever,
    substring_retriever,
)
from anagrapheus.chronicle.sections import Anchor, Section, SectionId
from anagrapheus.chronicle.sqlite import SQLiteChronicleStore
from anagrapheus.chronicle.store import ChronicleStore

__all__ = [
    "FOLD_NAME",
    "Anchor",
    "Anchors",
    "Brief",
    "ChronicleFold",
    "ChronicleStore",
    "Consolidation",
    "EventTypes",
    "Prose",
    "Recent",
    "Retriever",
    "SQLiteChronicleStore",
    "Section",
    "SectionId",
    "Summarizer",
    "UnitBoundary",
    "section_id",
    "substring_retriever",
]
