"""
Projection folds: the uniform shape every read model is built from.

A fold is ``(name, fold, apply(event, txn))`` — idempotent, or transactional against
``txn`` — and the projector keeps it position-guarded: boot replays above the
position, so a crash between batches costs a re-fold of at most one batch, never a
double-count.

**Fold-replay is an invariant only for deterministic folds.** Same log, same
projections holds while every fold is arithmetic over the stream, and stops holding the
moment a fold's ``apply`` makes a model call: the same log then yields *a* projection,
not *the* projection. Rather than let the invariant quietly acquire an exception, each
fold **declares its grade**, and the machinery treats the two differently where it
matters — see :attr:`ProjectionFold.fold` and :meth:`Projector.rebuild`.

The grade is a property of the fold protocol, not of the chronicle: a prose summariser
is the obvious generative fold, but so is a portrait regenerator or any other read
model whose content a model writes.
"""

# Design: note 05 §1-§2; upstream NVM R26, R61.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, Protocol, cast

from anagrapheus.timeline.errors import TimelineError
from anagrapheus.timeline.store import TransactionalStore

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.store import EventStore
    from anagrapheus.timeline.upcast import EventReader

__all__ = [
    "FoldDeterminism",
    "GenerativeFold",
    "GenerativeRebuild",
    "ProjectionFold",
    "Projector",
]

_log = logging.getLogger(__name__)

#: A fold's replay grade. ``"deterministic"`` folds satisfy fold-replay: same log, same
#: projection. ``"generative"`` folds do not — their ``apply`` draws from a model, so
#: the same log yields the same *anchors* and regenerated content.
type FoldDeterminism = Literal["deterministic", "generative"]


class ProjectionFold(Protocol):
    """One read model: a named, position-guarded fold over the stream."""

    @property
    def name(self) -> str:
        """The position key. Stable for the projection's lifetime."""
        ...

    @property
    def fold(self) -> FoldDeterminism:
        """
        Whether replaying this fold reproduces it, or merely re-derives it.

        **Required, and deliberately not defaulted.** A default would be
        ``"deterministic"``, and a generative fold that forgot to declare would inherit
        a guarantee it cannot keep — silently, since nothing about a model call looks
        different from the outside. The one failure this property exists to prevent is
        exactly that one, so every fold says which it is.

        ``"deterministic"`` — ``apply`` is a pure function of the event and prior state.
        Fold-replay holds: drop the projection, replay, get the identical thing back.
        This is what makes a backend swap "stand up the adapter and replay".

        ``"generative"`` — ``apply`` draws from a model. What replays exactly is the
        *anchors*: which sections exist, the event ranges they cite, their order. The
        content is regenerated, and is bit-identical only where the draw's pin still
        hits. Nothing that settles anything may read the generated content.
        """
        ...

    def apply(self, event: StoredEvent, txn: object) -> None:
        """
        Fold one event into the projection's state.

        Must be idempotent, or transactional against ``txn``. For state living in the
        same SQLite file, ``txn`` is the shared connection and the position commit
        rides the same transaction; for external state the fold must tolerate
        re-application of the last batch after a crash.
        """
        ...


class GenerativeRebuild(TimelineError):
    """A generative fold was asked to rebuild without acknowledging what that discards."""


class GenerativeFold(Protocol):
    """
    A fold whose content is drawn **before** the write transaction opens.

    :meth:`Projector._apply_batch` calls ``apply`` inside ``BEGIN IMMEDIATE``, so that a
    projection's state and its position commit together. For a deterministic fold that
    is free — the work is arithmetic. For a generative one it is not: the draw is a
    remote call, and holding SQLite's write lock across it blocks every append in the
    process for as long as the model takes to answer. Consolidation is *debt*, run in
    the background; blocking the hot path is the one thing it must not do.

    So a generative fold splits in two. :meth:`prepare` draws, outside any transaction.
    ``apply`` then writes what was prepared, under the lock, with nothing slow left in
    it. **Every generative fold must implement it**, and the projector calls it by the
    declared grade rather than by duck-typing, so a fold that forgot fails loudly.
    """

    def prepare(self, event: StoredEvent) -> None:
        """
        Draw whatever ``apply`` will need for this event. Runs with no lock held.

        Called for every event in the batch before the transaction opens, so a fold
        that has nothing to do for an event should return without drawing.
        """
        ...


def _validate_batch_size(batch_size: int) -> None:
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")


class Projector:
    """Dispatches the stream to registered folds, position-guarded, in batches."""

    def __init__(
        self,
        store: EventStore,
        reader: EventReader,
        folds: Sequence[ProjectionFold],
    ) -> None:
        names = [fold.name for fold in folds]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate fold names: {sorted(names)}")
        self._store = store
        self._reader = reader
        self._folds = list(folds)

    def catch_up(self, *, batch_size: int = 1000) -> dict[str, int]:
        """
        Fold everything above each fold's position; return name -> new position.

        This is boot replay: called at startup it brings every projection current,
        having replayed only what each one had not yet seen.

        *Every* registered fold runs. Projections that must not block the caller — a
        prose summariser, a portrait regenerator — belong to a **second projector**
        over their own fold list, driven on its own schedule; each fold keeps an
        independent position, so the two never interfere. That split is the caller's
        to make, and a fold that declared its own schedule could not be dispatched by
        anything anyway.
        """
        _validate_batch_size(batch_size)
        return {fold.name: self._run(fold, batch_size=batch_size) for fold in self._folds}

    def rebuild(
        self,
        name: str,
        *,
        reset: Callable[[], None],
        batch_size: int = 1000,
        reset_in_transaction: bool = False,
        discard_generated: bool = False,
    ) -> int:
        """
        Drop and re-fold one projection from the start of the stream.

        ``reset`` disposes the projection's state; the position returns to zero and
        the full stream replays. For a **deterministic** fold this is fold-replay as an
        operation — the reason a backend swap is "stand up the adapter, replay the log".

        **For a generative fold it is neither cheap nor exact, so it is refused** unless
        ``discard_generated=True`` says so out loud. Rebuilding one re-runs every model
        call it ever made — the whole cost again — and returns the same anchors with
        different content, so anything that read the old content read something that no
        longer exists. That asymmetry is the reason such a projection is better
        *carried* across a backend swap than rebuilt: it is the one read model whose
        loss is not free. Carrying it grants it no authority — a carried projection and
        a rebuilt one are equally valid, since neither is canon.

        Whether ``reset`` participates in the store's transaction is the caller's
        explicit declaration, because the projector cannot see where the state
        lives. Pass ``reset_in_transaction=True`` ONLY when the state is in the
        transactional store's own database — then the position zero and the state
        drop commit together, atomically. The default commits the position to zero
        first, in its own step, then resets: for external state (a file, another
        service) a crash between the two leaves a replay over stale state, which
        the fold contract already requires ``apply`` to tolerate — never a cleared
        projection under a high position that catch-up would skip past.
        """
        fold = next((f for f in self._folds if f.name == name), None)
        if fold is None:
            raise KeyError(f"no fold named {name!r}")
        if fold.fold == "generative" and not discard_generated:
            raise GenerativeRebuild(
                f"{name!r} is a generative fold: rebuilding re-runs every model call it "
                f"made and returns the same anchors with different content. Carry it "
                f"across instead, or pass discard_generated=True to accept both."
            )
        _validate_batch_size(batch_size)
        if reset_in_transaction:
            if not isinstance(self._store, TransactionalStore):
                raise TypeError(
                    "reset_in_transaction=True requires a TransactionalStore; "
                    f"{type(self._store).__name__} is not one"
                )
            with self._store.transaction():
                self._store.commit_position(name, 0)
                reset()
        else:
            self._store.commit_position(name, 0)
            reset()
        return self._run(fold, batch_size=batch_size)

    def _run(self, fold: ProjectionFold, *, batch_size: int) -> int:
        position = self._store.read_position(fold.name)
        while True:
            batch = list(self._reader.read_raw(since_seq=position, limit=batch_size))
            if not batch:
                return position
            position = self._apply_batch(fold, batch, position)

    def _apply_batch(self, fold: ProjectionFold, batch: list[StoredEvent], position: int) -> int:
        new_position = batch[-1].seq
        if fold.fold == "generative":
            # Outside the transaction, deliberately -- see `GenerativeFold`. A batch may
            # hold several boundaries, so this is where a whole batch's model calls
            # happen, and none of them is holding the write lock while it does.
            generative = cast("GenerativeFold", fold)
            for event in batch:
                generative.prepare(event)
        if isinstance(self._store, TransactionalStore):
            with self._store.transaction() as txn:
                for event in batch:
                    fold.apply(event, txn)
                self._store.commit_position(fold.name, new_position)
        else:
            for event in batch:
                fold.apply(event, None)
            self._store.commit_position(fold.name, new_position)
        _log.debug("fold %s: %d -> %d (%d event(s))", fold.name, position, new_position, len(batch))
        return new_position

    def positions(self) -> Iterator[tuple[str, int]]:
        """Yield each fold's name and current position."""
        for fold in self._folds:
            yield fold.name, self._store.read_position(fold.name)
