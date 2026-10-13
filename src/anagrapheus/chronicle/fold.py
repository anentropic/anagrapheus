"""
The chronicle's fold — the first generative projection in this library.

It watches for a **unit boundary** the application declares (a turn, a scene close, a
session end), and on each one consolidates everything since the last boundary into a
section citing the range it covers.

**Why the fold does not build the pin key.** Generative folds pin their model calls, and
a key built from stable identifiers alone *cannot notice a rewritten prompt* — it would
serve a stale pin to an edited consolidation. That failure is reachable from here: the
fold can see the event range but not the prompt, so any key it built would be blind in
exactly that way. So the key is built where the prompt is visible — inside the
:class:`Summarizer`, which typically draws through
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
transaction open before applying the batch on the calling thread. An async summarizer
needs the awaited path. A synchronous :class:`Summarizer` runs under either, but under
the awaited one it is called on the event loop's thread and blocks the loop while it
draws, and it cannot use a blocking model client that starts a loop of its own
(``Agent.run_sync`` raises there): a summarizer that calls a model from async code
should be an :class:`AsyncSummarizer`.
"""

# Design: note 05; upstream NVM R61 (clauses 1 and 5).

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, NamedTuple, Protocol

from anagrapheus.chronicle.sections import Anchor, Section

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

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

    The fold tells the two kinds apart **before** calling, so that the synchronous path
    refuses an async summarizer without starting its draw. It recognises an ``async def``
    function, a callable object whose ``__call__`` is ``async def``, and anything marked
    with :func:`inspect.markcoroutinefunction`. Write it as one of those: a plain
    function that returns a task or other awaitable is only discovered once called, and
    the synchronous path then has to cancel what it started.
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


def _awaits(summarize: Callable[..., object]) -> bool:
    """Whether ``summarize`` is recognisably async, judged without calling it."""
    call = type(summarize).__call__  # the object's own, where it is a callable instance
    return inspect.iscoroutinefunction(summarize) or inspect.iscoroutinefunction(call)


def _discard(drawn: object) -> None:
    """Stop an awaitable the synchronous path cannot await, so it draws nothing."""
    if inspect.iscoroutine(drawn):
        drawn.close()  # never started, so nothing was drawn
    elif asyncio.isfuture(drawn):
        drawn.cancel()


class ChronicleFold:
    """
    Consolidates each closed unit into a section. Declared **generative**.

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
        self._awaits = _awaits(summarize)
        self._name = name
        # What `prepare` drew, waiting for `apply` to write it, keyed by boundary seq.
        # `apply` consumes its own entry, so a batch that fails part-way leaves the
        # entries it never reached behind; a retry draws those units again and
        # overwrites them, or drops them if the unit was covered in the meantime.
        self._prepared: dict[int, tuple[int, Consolidation]] = {}

    @property
    def name(self) -> str:
        """The position key."""
        return self._name

    @property
    def prepares_synchronously(self) -> bool:
        """
        Whether :meth:`prepare` can draw: ``False`` for an :class:`AsyncSummarizer`.

        The projector asks before doing anything, so ``catch_up()`` and ``rebuild()``
        refuse an async summarizer before folding or resetting anything.
        """
        return not self._awaits

    def prepare(self, event: StoredEvent) -> None:
        """
        Draw this unit's consolidation, **outside the write transaction**.

        The model call lives here rather than in ``apply`` because ``apply`` runs under
        ``BEGIN IMMEDIATE``: a remote call there holds SQLite's write lock for its whole
        duration and blocks every append in the process. Consolidation is debt, run in
        the background — blocking the hot path is the one thing it must not do.

        The range is read here too, so a unit already covered costs no call at all.

        Raises :class:`TypeError` if the summarizer is an :class:`AsyncSummarizer`,
        which only :meth:`prepare_async` can await. A recognisably async one is refused
        without being called; one discovered only by what it returned has that coroutine
        closed or that task cancelled before the error is raised.
        """
        if self._awaits:
            raise self._needs_awaiting()
        unit = self._unit(event)
        if unit is None:
            return
        start, events = unit
        drawn = self._summarize(events)
        if inspect.isawaitable(drawn):
            _discard(drawn)
            raise self._needs_awaiting()
        self._keep(start, event, drawn)

    def _needs_awaiting(self) -> TypeError:
        return TypeError(
            f"{self._name!r} has an async summarizer; drive its projector with catch_up_async()"
        )

    async def prepare_async(self, event: StoredEvent) -> None:
        """
        The awaited twin of :meth:`prepare`, for the awaited catch-up path.

        Takes either kind of summarizer: an async one is awaited, a synchronous one is
        called. The synchronous call runs on the event loop's thread and blocks the loop
        until it returns, so it must not start a loop of its own (``Agent.run_sync``
        raises here); a summarizer that calls a model belongs in an
        :class:`AsyncSummarizer`. Everything else — the range, the skip for a unit
        already covered, what ``apply`` is left to write — is :meth:`prepare`'s.
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
        # rest of the answer, and they clear themselves as `apply` consumes them. Only
        # those below this event count: any above it were left by a failed batch, whose
        # writes rolled back, and are redrawn when the retry reaches them.
        prior = (seq for seq in self._prepared if seq < event.seq)
        start = max([self._store.consolidated_through(), *prior]) + 1
        if event.seq < start:
            # Already covered -- the fold contract allows the last batch to be
            # re-applied after a crash, and re-consolidating would spend a model call
            # to produce a second valid answer for a range that already has one. A draw a
            # failed batch left here is stale too, and `apply` must not write it.
            self._prepared.pop(event.seq, None)
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
