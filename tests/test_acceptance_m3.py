"""
Milestone-3 acceptance: the belief layer, end to end, one story.

An agent believes things about two contacts, learns they are one person, and finds out
exactly which beliefs that invalidated — the re-key seam between identity and belief.
Then a second holder disagrees with it, which is the divergence query.

Beliefs are folded into a real doxastica core, not a fake: the point of the milestone
is the integration, and a fake would only prove we agree with ourselves. They are
folded from the log, so the story ends by losing them and getting them back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    BeliefEvents,
    BeliefFold,
    BeliefIndex,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    SQLiteBeliefIndexStore,
    divergence,
    plan_rekeys,
)
from anagrapheus.beliefs.fold import FOLD_NAME as BELIEFS_FOLD
from anagrapheus.identity import (
    IdentityFold,
    IdentityRegistry,
    Ladder,
    SQLiteIdentityStore,
    establish,
    merge,
)
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    PinLedger,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from pathlib import Path

LADDER = Ladder(("doubted", "suspected", "believed", "certain"))

# The application's predicates, declared in code because they are schema.
PREDICATES = (
    Predicate("location", 1, PredicateKind.FUNCTIONAL),
    Predicate("knows", 2, PredicateKind.RELATIONAL),
)


def open_stack(path: Path, types: EventTypeRegistry, predicates: PredicateRegistry):
    """Everything wired over one file, as a composition root would do it."""
    store = SQLiteEventStore(path)
    identity_store = SQLiteIdentityStore(store.connection)
    index_store = SQLiteBeliefIndexStore(store.connection)
    reader = EventReader(store, UpcastRegistry(), types)
    beliefs = BeliefFold(
        MemoryCore(InMemoryBackend()), predicates, index_store, types, durable=False
    )
    projector = Projector(store, reader, [IdentityFold(identity_store, types), beliefs])
    return (
        store,
        beliefs,
        projector,
        IdentityRegistry(identity_store, ladder=LADDER),
        BeliefIndex(index_store),
    )


def test_milestone_3_acceptance(tmp_path: Path):
    path = tmp_path / "agent.db"
    types = EventTypeRegistry()
    register_builtin_events(types)
    predicates = PredicateRegistry(PREDICATES, namespace="crm")
    store, beliefs, projector, identity, index = open_stack(path, types, predicates)
    events = BeliefEvents(types, predicates)

    def core() -> MemoryCore:
        assert isinstance(beliefs.store, MemoryCore)
        return beliefs.store

    # -- two contacts and a colleague, every id minted through the pin ledger ---------
    ledger = PinLedger(store, types)
    contact_a, e_a = establish(types, "alice@corp", provenance="crm", resolver=ledger)
    contact_b, e_b = establish(types, "Alice Smith", provenance="email", resolver=ledger)
    carol, e_c = establish(types, "carol@corp", provenance="crm", resolver=ledger)
    store.append([e_a, e_b, e_c])

    # -- the agent believes things, each one an event in the log ----------------------
    #
    # Each belief event names the event that occasioned it as its cause, so "which event
    # caused this?" is answered by the envelope, the way the rest of the stack answers
    # it. Here that is the establishing event.
    established = [e.envelope.event_id for e in store.read()]
    store.append(
        [
            events.revise("agent", "location", (contact_a,), "London", causation_id=established[0]),
            events.revise(
                "agent",
                "knows",
                (contact_b, carol),
                True,
                stance=Stance.suspected,
                causation_id=established[1],
            ),
            events.revise("agent", "location", (carol,), "Paris", causation_id=established[2]),
        ]
    )
    projector.catch_up()

    assert len(core().query_scope("agent", BeliefFilter())) == 3

    # -- the two contacts turn out to be one person ----------------------------------
    before_merge = store.last_seq()
    store.append([merge(types, kept=contact_a, absorbed=contact_b)])
    projector.catch_up()
    merged_at = list(store.read(since_seq=before_merge))[0].envelope.event_id

    # -- and the stack answers the question doc 00 §5d asks --------------------------
    rekeys = identity.rekeys_since(before_merge)
    plans = plan_rekeys(rekeys, index, predicates)

    assert [p.belief_id for p in plans] == [predicates.belief_id("knows", contact_b, carol)]
    assert plans[0].sole() == predicates.belief_id("knows", contact_a, carol)

    # The belief about carol is untouched: she was not party to the merge.
    assert predicates.belief_id("location", carol) not in {p.belief_id for p in plans}

    # -- applying the plan is the caller's, because it is a belief decision ----------
    for plan in plans:
        stale = next(
            s for s in core().query_scope("agent", BeliefFilter()) if s.belief_id == plan.belief_id
        )
        old, new = predicates.parse(plan.belief_id), predicates.parse(plan.sole())
        store.append(
            [
                events.revise(
                    "agent",
                    new.name,
                    new.entity_ids,
                    stale.value,
                    stance=stale.stance,
                    causation_id=merged_at,
                ),
                events.contract("agent", old.name, old.entity_ids, causation_id=merged_at),
            ]
        )
    projector.catch_up()

    held = {s.belief_id for s in core().query_scope("agent", BeliefFilter())}
    assert predicates.belief_id("knows", contact_a, carol) in held
    assert predicates.belief_id("knows", contact_b, carol) not in held

    # -- a second holder disagrees, which is the whole multi-agent case --------------
    store.append([events.revise("rumour", "location", (carol,), "Berlin", causation_id=merged_at)])
    projector.catch_up()

    assert [(d.held, d.reference) for d in divergence(core(), "agent", "rumour")] == [
        ("Paris", "Berlin")
    ]
    before_crash = (_snapshot(store), _beliefs(core()))

    # -- crash: a fresh process, with nothing of the beliefs but the log -------------
    #
    # This store is in memory, so it went with the process. The fold was told so
    # (`durable=False`), and kept its position with the beliefs rather than in the
    # file, so the fresh process starts from zero and catching up folds the whole log
    # again. No rebuild: everything comes back.
    ledger.commit()
    store.close()

    fresh_types = EventTypeRegistry()
    register_builtin_events(fresh_types)
    store, beliefs, projector, identity, index = open_stack(path, fresh_types, predicates)
    assert projector.position(BELIEFS_FOLD) == 0
    assert core().query_scope("agent", BeliefFilter()) == []
    projector.catch_up()
    assert projector.position(BELIEFS_FOLD) == store.last_seq()

    # -- refolded: same log, same index, same beliefs --------------------------------
    assert (_snapshot(store), _beliefs(core())) == before_crash
    assert [(d.held, d.reference) for d in divergence(core(), "agent", "rumour")] == [
        ("Paris", "Berlin")
    ]

    # ...and the index, replayed over itself, still answers the merge question.
    assert {r.belief_id for r in index.beliefs_for(carol)} == {
        predicates.belief_id("location", carol),
        predicates.belief_id("knows", contact_b, carol),
        predicates.belief_id("knows", contact_a, carol),
    }
    store.close()


def test_the_relational_trap_is_unrepresentable(tmp_path: Path):
    """
    Keying a relation on one side only, ruled out from both ends.

    It cannot be minted at the wrong arity, and it cannot be written with the third
    party hidden in the value — so revising a claim about bob can never silently
    retract one about carol.
    """
    predicates = PredicateRegistry(PREDICATES, namespace="crm")
    types = EventTypeRegistry()
    register_builtin_events(types)
    store, beliefs, projector, _, _ = open_stack(tmp_path / "agent.db", types, predicates)
    events = BeliefEvents(types, predicates)
    try:
        alice, _ = establish(types, "alice", provenance="crm")
        bob, _ = establish(types, "bob", provenance="crm")
        carol, _ = establish(types, "carol", provenance="crm")

        with pytest.raises(Exception, match="takes 2 entities"):
            predicates.belief_id("knows", alice)
        with pytest.raises(TypeError, match="relational"):
            events.revise("agent", "knows", (alice, bob), str(carol))

        store.append(
            [
                events.revise("agent", "knows", (alice, bob), True),
                events.revise("agent", "knows", (alice, carol), True),
            ]
        )
        projector.catch_up()
        assert len(beliefs.store.query_scope("agent", BeliefFilter())) == 2
    finally:
        store.close()


def _snapshot(store: SQLiteEventStore) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: sorted(tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in ("belief_ids", "belief_entities")
    }


def _beliefs(core: MemoryCore) -> list[tuple[object, ...]]:
    # Everything but `state_id`, which doxastica mints afresh on every fold.
    return [
        (s.scope_id, s.belief_id, s.source_event_id, repr(s.value), s.status, s.stance)
        for scope in ("agent", "rumour")
        for t in sorted(
            core.query_scope(scope, BeliefFilter(), include_retracted=True),
            key=lambda t: t.belief_id,
        )
        for s in core.get_revision_chain(t.belief_id)
        if s.scope_id == scope
    ]
