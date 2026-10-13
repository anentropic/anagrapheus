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

**Why the draw can be awaited and ``apply`` cannot.** ``apply`` runs inside the
projector's write transaction and only writes what was drawn, so it has nothing to
await. The draw is the slow part, and the one an async application cannot make
synchronously: a blocking model call refuses to run inside a running event loop, and the
store cannot follow it to a worker thread because a SQLite connection belongs to the
thread that opened it. So the summarizer may be an :class:`AsyncSummarizer`, the fold
awaits it in :meth:`ChronicleFold.prepare_async`, and
:meth:`~anagrapheus.timeline.folds.Projector.catch_up_async` awaits that with no
transaction open before applying the batch on the calling thread. A synchronous
:class:`Summarizer` works under either path; an async one needs the awaited path.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, NamedTuple, Protocol

from anagrapheus.chronicle.sections import Anchor, Section

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from anagrapheus.chronicle.sections import SectionId
    from anagrapheus.chronicle.store import ChronicleStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.folds import FoldDeterminism
    from anagrapheus.timeline.pins import PinKey
    from anagrapheus.timeline.upcast import EventReader

__all__ = [
    "AsyncSummarizer",
    "Consolidation",
    "ChronicleFold",
    "Summarizer",
    "UnitBoundary",
    "FOLD_NAME",
]

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


class AsyncSummarizer(Protocol):
    """
    A :class:`Summarizer` whose draw is awaited: an ``async def``, or any callable returning one.

    The shape an async application will want, since the model calls it makes are
    coroutines. A fold given one must be driven by
    :meth:`~anagrapheus.timeline.folds.Projector.catch_up_async`.
    """

    def __call__(self, events: Sequence[StoredEvent]) -> Awaitable[Consolidation]:
        """
        Consolidate ``events`` into a section's prose, awaitably.

        The same contract as :meth:`Summarizer.__call__`. Drawing through a pinned
        model's async path resolves its pins with the ledger's awaited resolve, on the
        loop's own thread.
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
        summarize: Summarizer | AsyncSummarizer,
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

        Raises :class:`TypeError` if the summarizer is an :class:`AsyncSummarizer`,
        which only :meth:`prepare_async` can await.
        """
        unit = self._unit(event)
        if unit is None:
            return
        start, events = unit
        drawn = self._summarize(events)
        if inspect.isawaitable(drawn):
            if inspect.iscoroutine(drawn):
                drawn.close()  # never started, so nothing was drawn
            raise TypeError(
                f"{self._name!r} has an async summarizer; drive its projector with catch_up_async()"
            )
        self._keep(start, event, drawn)

    async def prepare_async(self, event: StoredEvent) -> None:
        """
        The awaited twin of :meth:`prepare`, for the awaited catch-up path.

        Takes either kind of summarizer: an async one is awaited, a synchronous one is
        called. Everything else — the range, the skip for a unit already covered, what
        ``apply`` is left to write — is :meth:`prepare`'s.
        """
        unit = self._unit(event)
        if unit is None:
            return
        start, events = unit
        drawn = self._summarize(events)
        if inspect.isawaitable(drawn):
            drawn = await drawn
        self._keep(start, event, drawn)

    def _unit(self, event: StoredEvent) -> tuple[int, list[StoredEvent]] | None:
        """Where the unit ``event`` closes starts, and its events; ``None`` if nothing to draw."""
        if not self._boundary.closes_unit(event):
            return None
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
            return None
        events = list(self._reader.read_raw(since_seq=start - 1, limit=event.seq - start + 1))
        return (start, events) if events else None

    def _keep(self, start: int, event: StoredEvent, drawn: Consolidation) -> None:
        """Hold a drawn consolidation for ``apply`` to write."""
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
