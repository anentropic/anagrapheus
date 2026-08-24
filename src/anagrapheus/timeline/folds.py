"""
Projection folds: the uniform shape every read model is built from.

A fold is ``(name, clock, apply(event, txn))`` — idempotent, or transactional against
``txn`` — and the projector keeps it watermark-guarded: boot replays above the
watermark, so a crash between batches costs a re-fold of at most one batch, never a
double-count. Fold-replay is an invariant (same log, same projections), and
:meth:`Projector.rebuild` makes it an operation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from anagrapheus.timeline.store import TransactionalStore

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.store import EventStore
    from anagrapheus.timeline.upcast import EventReader

__all__ = ["ProjectionFold", "Projector"]


class ProjectionFold(Protocol):
    """One read model: a named, watermark-guarded fold over the stream."""

    @property
    def name(self) -> str:
        """The watermark key. Stable for the projection's lifetime."""
        ...

    @property
    def clock(self) -> Literal["sync", "async"]:
        """When the fold runs. Only ``"sync"`` is dispatched in milestone 1."""
        ...

    def apply(self, event: StoredEvent, txn: object) -> None:
        """
        Fold one event into the projection's state.

        Must be idempotent, or transactional against ``txn``. For state living in the
        same SQLite file, ``txn`` is the shared connection and the watermark commit
        rides the same transaction; for external state the fold must tolerate
        re-application of the last batch after a crash.
        """
        ...


def _validate_batch_size(batch_size: int) -> None:
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")


class Projector:
    """Dispatches the stream to registered folds, watermark-guarded, in batches."""

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
        Fold everything above each fold's watermark; return name -> new watermark.

        This is boot replay: called at startup it brings every projection current,
        having replayed only what each one had not yet seen.
        """
        _validate_batch_size(batch_size)
        result: dict[str, int] = {}
        for fold in self._folds:
            if fold.clock != "sync":
                result[fold.name] = self._store.read_watermark(fold.name)
                continue
            result[fold.name] = self._run(fold, batch_size=batch_size)
        return result

    def rebuild(
        self,
        name: str,
        *,
        reset: Callable[[], None],
        batch_size: int = 1000,
        reset_in_transaction: bool = False,
    ) -> int:
        """
        Drop and re-fold one projection from the start of the stream.

        ``reset`` disposes the projection's state; the watermark returns to zero and
        the full stream replays. Fold-replay as an operation — this is why a backend
        swap is "stand up the adapter, replay the log".

        Whether ``reset`` participates in the store's transaction is the caller's
        explicit declaration, because the projector cannot see where the state
        lives. Pass ``reset_in_transaction=True`` ONLY when the state is in the
        transactional store's own database — then the watermark zero and the state
        drop commit together, atomically. The default commits the watermark to zero
        first, in its own step, then resets: for external state (a file, another
        service) a crash between the two leaves a replay over stale state, which
        the fold contract already requires ``apply`` to tolerate — never a cleared
        projection under a high watermark that catch-up would skip past.
        """
        fold = next((f for f in self._folds if f.name == name), None)
        if fold is None:
            raise KeyError(f"no fold named {name!r}")
        _validate_batch_size(batch_size)
        if reset_in_transaction:
            if not isinstance(self._store, TransactionalStore):
                raise TypeError(
                    "reset_in_transaction=True requires a TransactionalStore; "
                    f"{type(self._store).__name__} is not one"
                )
            with self._store.transaction():
                self._store.commit_watermark(name, 0)
                reset()
        else:
            self._store.commit_watermark(name, 0)
            reset()
        return self._run(fold, batch_size=batch_size)

    def _run(self, fold: ProjectionFold, *, batch_size: int) -> int:
        watermark = self._store.read_watermark(fold.name)
        while True:
            batch = list(self._reader.read_raw(since_seq=watermark, limit=batch_size))
            if not batch:
                return watermark
            watermark = self._apply_batch(fold, batch, watermark)

    def _apply_batch(self, fold: ProjectionFold, batch: list[StoredEvent], watermark: int) -> int:
        new_watermark = batch[-1].seq
        if isinstance(self._store, TransactionalStore):
            with self._store.transaction() as txn:
                for event in batch:
                    fold.apply(event, txn)
                self._store.commit_watermark(fold.name, new_watermark)
        else:
            for event in batch:
                fold.apply(event, None)
            self._store.commit_watermark(fold.name, new_watermark)
        return new_watermark

    def watermarks(self) -> Iterator[tuple[str, int]]:
        """Yield each fold's name and current watermark."""
        for fold in self._folds:
            yield fold.name, self._store.read_watermark(fold.name)
