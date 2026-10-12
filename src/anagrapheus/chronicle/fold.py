"""
The chronicle's fold — the first generative projection in this library.

It watches for a **unit boundary** the application declares (a turn, a scene close, a
session end), and on each one consolidates everything since the last boundary into a
section citing the range it covers.

**Why the fold does not build the pin key.** R61 clause 5 pins generative folds, and
notes that a key built from stable identifiers alone *cannot notice a rewritten
prompt* — it would serve a stale pin to an edited consolidation. That failure is
reachable from here: the fold can see the event range but not the prompt, so any key it
built would be blind in exactly that way. So the key is built where the prompt is
visible — inside the :class:`Summarizer`, which typically draws through
:class:`~anagrapheus.provider.model.PinnedModel` and gets the whole request keyed for
free. The fold records the key it is handed and asserts nothing about it.

**Why ``apply`` stays synchronous.** ``ProjectionFold.apply`` is sync, and consolidation
is debt — a background pass, not a turn's hot path — so blocking inside it costs
nothing that matters. An async model call is the summarizer's to bridge, and it is
already running on a projector of its own.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple, Protocol

from anagrapheus.chronicle.sections import Anchor, Section

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anagrapheus.chronicle.sections import SectionId
    from anagrapheus.chronicle.store import ChronicleStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.folds import FoldDeterminism
    from anagrapheus.timeline.pins import PinKey
    from anagrapheus.timeline.upcast import EventReader

__all__ = ["Consolidation", "ChronicleFold", "Summarizer", "UnitBoundary", "FOLD_NAME"]

_log = logging.getLogger(__name__)

#: The default position key.
FOLD_NAME = "chronicle"


class Consolidation(NamedTuple):
    """What a summarizer returns: the prose, and the pins its draws resolved under."""

    prose: str
    #: Plural because consolidating a range may take several calls, and empty when the
    #: summarizer drew outside a ledger. Worth recording either way: a section with no
    #: pins can be shown to have rebuilt, never to have rebuilt *bit-exact*.
    #:
    #: Obtainable from :class:`~anagrapheus.provider.model.PinnedModel` by passing it an
    #: ``on_key`` recorder — the model computes the key internally, so without that hook
    #: a summarizer would have to duplicate the policy to learn it.
    pins: tuple[PinKey, ...] = ()


class Summarizer(Protocol):
    """Turns one range of events into prose. The application's, always."""

    def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        """
        Consolidate ``events`` into a section's prose.

        Expected to draw through a :class:`~anagrapheus.timeline.pins.PinResolver` and
        return the resulting key — see the module docstring for why the key belongs
        here rather than in the fold.
        """
        ...


class UnitBoundary(Protocol):
    """Decides which events close a unit of work. The application's vocabulary."""

    def closes_unit(self, event: StoredEvent) -> bool:
        """Whether this event ends a unit, and so triggers a consolidation."""
        ...


class EventTypes:
    """A :class:`UnitBoundary` naming the marker types outright."""

    def __init__(self, *types: tuple[str, str]) -> None:
        self._types = frozenset(types)

    def closes_unit(self, event: StoredEvent) -> bool:
        """Whether the event's ``(namespace, type)`` is one of the declared markers."""
        return (event.envelope.namespace, event.envelope.type) in self._types


class ChronicleFold:
    """
    Consolidates each closed unit into a section. **Generative**, per R61 clause 1.

    What replays exactly is the anchors: which ranges were consolidated, where their
    boundaries fall, and in what order — all of that is a pure function of the log. The
    prose is regenerated, and is bit-identical only where the summarizer's pin still
    hits.
    """

    fold: FoldDeterminism = "generative"

    def __init__(
        self,
        store: ChronicleStore,
        reader: EventReader,
        *,
        boundary: UnitBoundary,
        summarize: Summarizer,
        name: str = FOLD_NAME,
    ) -> None:
        self._store = store
        self._reader = reader
        self._boundary = boundary
        self._summarize = summarize
        self._name = name
        # What `prepare` drew, waiting for `apply` to write it. Emptied as it is
        # consumed, so it never holds more than one batch's worth.
        self._prepared: dict[int, tuple[int, Consolidation]] = {}

    @property
    def name(self) -> str:
        """The position key."""
        return self._name

    def prepare(self, event: StoredEvent) -> None:
        """
        Draw this unit's consolidation, **outside the write transaction**.

        The model call lives here rather than in ``apply`` because ``apply`` runs under
        ``BEGIN IMMEDIATE``: a remote call there holds SQLite's write lock for its whole
        duration and blocks every append in the process. Consolidation is debt, run in
        the background — blocking the hot path is the one thing it must not do.

        The range is read here too, so a unit already covered costs no call at all.
        """
        if not self._boundary.closes_unit(event):
            return
        # The store's watermark alone is not enough: a batch can hold several
        # boundaries, and every `prepare` in it runs before any `apply` writes. Reading
        # only `consolidated_through()` would give each of them the same start, so the
        # second would claim a range overlapping the first. The pending draws are the
        # rest of the answer, and they clear themselves as `apply` consumes them.
        start = max([self._store.consolidated_through(), *self._prepared]) + 1
        if event.seq < start:
            # Already covered -- the fold contract allows the last batch to be
            # re-applied after a crash, and re-consolidating would spend a model call
            # to produce a second valid answer for a range that already has one.
            return
        events = list(self._reader.read_raw(since_seq=start - 1, limit=event.seq - start + 1))
        if not events:
            return
        drawn = self._summarize(events)
        _log.info("consolidated seq %d..%d: %s", start, event.seq, drawn.prose)
        self._prepared[event.seq] = (start, drawn)

    def apply(self, event: StoredEvent, txn: object) -> None:
        """Write what :meth:`prepare` drew. Nothing slow happens here."""
        drawn = self._prepared.pop(event.seq, None)
        if drawn is None:
            return
        start, consolidation = drawn
        self._store.append(
            Section(
                anchor=Anchor(
                    section_id=section_id(start, event.seq),
                    from_seq=start,
                    to_seq=event.seq,
                    unit=event.seq,
                    supersedes=None,
                ),
                prose=consolidation.prose,
                pins=consolidation.pins,
            )
        )


def section_id(from_seq: int, to_seq: int, *, generation: int = 1) -> SectionId:
    """
    A section's id, derived from the range it covers.

    Derived rather than random **because the anchors must replay exactly**: a rebuild
    re-consolidates the same ranges, and ids drawn from a counter or a clock would make
    every anchor differ while the boundaries stayed identical — an invariant that holds
    in fact and fails its own test.

    ``generation`` distinguishes a deliberate re-consolidation of a range already
    covered, which supersedes rather than replaces. Such a section is *not* fold-derived
    and so is outside the anchor-replay claim: rebuilding discards it and returns
    generation 1. That is one of the things carrying a chronicle preserves and
    rebuilding does not.
    """
    suffix = "" if generation == 1 else f"#{generation}"
    return f"{from_seq}-{to_seq}{suffix}"
