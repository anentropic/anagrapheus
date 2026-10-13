"""
A whole negotiation's beliefs, dropped and folded back from the log.

Note 03 §9's claim on the demo rather than on a rig: every belief every party holds —
seeded, overheard, concluded by a model, re-keyed — comes back from the log alone, and
so does every dependency between them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doxastica import BeliefFilter, Status
from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate
from examples.negotiation.table import Table

from anagrapheus.timeline import ReplayPins, UpcastRegistry, read_recording
from tests.fixtures.record import NEGOTIATION

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from doxastica import MemoryCore


def _replayed(path: Path) -> Table:
    recording = read_recording(NEGOTIATION)
    run = negotiate(
        path,
        models=scripted(lambda _: None),
        resolver=lambda table: ReplayPins.from_events(
            recording.events, table.types, upcasts=UpcastRegistry(), on_miss="strict"
        ),
    )
    return run.table


type Key = tuple[str, str, object]
type Snapshot = tuple[list[tuple[object, ...]], list[tuple[Key, tuple[Key, ...]]]]


def _everything(core: MemoryCore, scopes: list[str]) -> Snapshot:
    # Every revision in every scope, and what each one rests on — by belief, since
    # `state_id` is doxastica's and new on every fold.
    by_id: dict[UUID, Key] = {}
    rows: list[tuple[object, ...]] = []
    for scope in scopes:
        tails = core.query_scope(
            scope, BeliefFilter(status=frozenset({Status.active, Status.retracted}))
        )
        for belief_id in sorted({t.belief_id for t in tails}):
            for s in core.get_revision_chain(belief_id):
                if s.scope_id == scope:
                    by_id[s.state_id] = (s.scope_id, s.belief_id, s.source_event_id)
                    rows.append(
                        (s.scope_id, s.belief_id, s.source_event_id, repr(s.value), s.status)
                    )
    impact = sorted(
        (key, tuple(sorted(by_id[r.state_id] for r in core.get_impact(sid).reached)))
        for sid, key in by_id.items()
    )
    return rows, impact


def test_a_played_negotiation_rebuilds_to_the_same_beliefs(tmp_path: Path):
    table = _replayed(tmp_path / "rebuild.db")
    try:
        before = _everything(table.core, table.scopes())
        _, impact = before
        assert any(reached for _, reached in impact), "the story lays edges; they are compared too"

        table.rebuild_beliefs()

        assert _everything(table.core, table.scopes()) == before
    finally:
        table.close()


def test_a_reopened_table_holds_what_it_held(tmp_path: Path):
    """The beliefs are in memory and the position is in the file; opening reconciles them."""
    path = tmp_path / "reopen.db"
    table = _replayed(path)
    try:
        scopes = table.scopes()
        before = _everything(table.core, scopes)
    finally:
        table.close()

    reopened = Table.open(path)
    try:
        assert _everything(reopened.core, scopes) == before
    finally:
        reopened.close()


def test_a_reopened_table_folds_its_beliefs_back(tmp_path: Path):
    """No rebuild on open: the fold is not durable, so a fresh process starts it at zero."""
    path = tmp_path / "reopen.db"
    table = _replayed(path)
    try:
        scopes = table.scopes()
        before = _everything(table.core, scopes)
    finally:
        table.close()

    reopened = Table.open(path)
    try:
        assert _everything(reopened.core, scopes) == before
    finally:
        reopened.close()
