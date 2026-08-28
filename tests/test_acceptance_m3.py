"""
Milestone-3 acceptance: the belief layer, end to end, one story.

An agent believes things about two contacts, learns they are one person, and finds out
exactly which beliefs that invalidated — the seam doc 00 §5d promises and §6 pays for.
Then a second holder disagrees with it, which is §6c.

Beliefs are written into a real doxastica core, not a fake: the point of the milestone
is the integration, and a fake would only prove we agree with ourselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    BeliefIndex,
    BeliefIndexFold,
    BeliefWriter,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    SQLiteBeliefIndexStore,
    divergence,
    mint,
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


def open_stack(path: Path, types: EventTypeRegistry):
    """Everything wired over one file, as a composition root would do it."""
    store = SQLiteEventStore(path)
    identity_store = SQLiteIdentityStore(store.connection)
    index_store = SQLiteBeliefIndexStore(store.connection)
    reader = EventReader(store, UpcastRegistry(), types)
    projector = Projector(
        store,
        reader,
        [IdentityFold(identity_store, types), BeliefIndexFold(index_store, types)],
    )
    return (
        store,
        index_store,
        projector,
        IdentityRegistry(identity_store, ladder=LADDER),
        BeliefIndex(index_store),
    )


def test_milestone_3_acceptance(tmp_path: Path):
    path = tmp_path / "agent.db"
    types = EventTypeRegistry()
    register_builtin_events(types)
    predicates = PredicateRegistry(PREDICATES, namespace="crm")
    store, index_store, projector, identity, index = open_stack(path, types)
    core = MemoryCore(InMemoryBackend())
    agent = BeliefWriter(core, predicates, scope_id="agent")

    # -- two contacts and a colleague, every id minted through the pin ledger ---------
    ledger = PinLedger(store, types)
    contact_a, e_a = establish(types, "alice@corp", provenance="crm", resolver=ledger)
    contact_b, e_b = establish(types, "Alice Smith", provenance="email", resolver=ledger)
    carol, e_c = establish(types, "carol@corp", provenance="crm", resolver=ledger)
    store.append([e_a, e_b, e_c])

    # -- the agent believes things, minting each id and recording the mint ------------
    _, m1 = mint(types, predicates, "location", contact_a)
    _, m2 = mint(types, predicates, "knows", contact_b, carol)
    _, m3 = mint(types, predicates, "location", carol)
    store.append([m1, m2, m3])
    projector.catch_up()

    # doxastica's `source_event_id` is a caller-supplied opaque handle, and the
    # envelope id of the event that occasioned the belief is exactly the handle worth
    # supplying: it makes "which event caused this?" answerable, and puts doxastica's
    # as-of ordering in step with the log's own.
    events = list(store.read())
    agent.revise("location", (contact_a,), "London", events[3].envelope.event_id)
    agent.revise("knows", (contact_b, carol), True, events[4].envelope.event_id, Stance.suspected)
    agent.revise("location", (carol,), "Paris", events[5].envelope.event_id)

    assert len(core.query_scope("agent", BeliefFilter())) == 3

    # -- the two contacts turn out to be one person ----------------------------------
    before_merge = store.last_seq()
    store.append([merge(types, kept=contact_a, absorbed=contact_b)])
    projector.catch_up()

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
            s for s in core.query_scope("agent", BeliefFilter()) if s.belief_id == plan.belief_id
        )
        successor = predicates.parse(plan.sole())
        _, minted = mint(types, predicates, successor.name, *successor.entity_ids)
        seq = store.append([minted])
        rekeyed = list(store.read(since_seq=seq - 1))[0]
        agent.revise(
            successor.name,
            successor.entity_ids,
            stale.value,
            rekeyed.envelope.event_id,
            stale.stance,
        )
        core.contract("agent", plan.belief_id, rekeyed.envelope.event_id)
    projector.catch_up()

    held = {s.belief_id for s in core.query_scope("agent", BeliefFilter())}
    assert predicates.belief_id("knows", contact_a, carol) in held
    assert predicates.belief_id("knows", contact_b, carol) not in held

    # -- a second holder disagrees, which is the whole multi-agent case --------------
    rumour = agent.for_scope("rumour")
    rumour.revise("location", (carol,), "Berlin", events[5].envelope.event_id)

    assert [(d.held, d.reference) for d in divergence(core, "agent", "rumour")] == [
        ("Paris", "Berlin")
    ]

    # -- crash: a fresh process resumes above the position ---------------------------
    ledger.commit()
    position_before = store.read_position(BELIEFS_FOLD)
    store.close()

    fresh_types = EventTypeRegistry()
    register_builtin_events(fresh_types)
    store, index_store, projector, identity, index = open_stack(path, fresh_types)
    assert store.read_position(BELIEFS_FOLD) == position_before
    projector.catch_up()
    incremental = _snapshot(store)

    # -- rebuild: same log, same index -----------------------------------------------
    projector.rebuild(BELIEFS_FOLD, reset=index_store.reset, reset_in_transaction=True)
    assert _snapshot(store) == incremental

    # ...and the index still answers the merge question after being rebuilt from zero.
    assert {r.belief_id for r in index.beliefs_for(carol)} == {
        predicates.belief_id("location", carol),
        predicates.belief_id("knows", contact_b, carol),
        predicates.belief_id("knows", contact_a, carol),
    }
    store.close()


def test_the_relational_trap_is_unrepresentable(tmp_path: Path):
    """
    The failure doc 00 §6a warns about, from both ends.

    It cannot be minted at the wrong arity, and it cannot be written with the third
    party hidden in the value — so revising a claim about bob can never silently
    retract one about carol.
    """
    predicates = PredicateRegistry(PREDICATES, namespace="crm")
    core = MemoryCore(InMemoryBackend())
    writer = BeliefWriter(core, predicates, scope_id="agent")
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(tmp_path / "agent.db")
    try:
        alice, _ = establish(types, "alice", provenance="crm")
        bob, _ = establish(types, "bob", provenance="crm")
        carol, _ = establish(types, "carol", provenance="crm")

        with pytest.raises(Exception, match="takes 2 entities"):
            predicates.belief_id("knows", alice)
        with pytest.raises(TypeError, match="relational"):
            writer.revise("knows", (alice, bob), carol, alice)

        writer.revise("knows", (alice, bob), True, alice)
        writer.revise("knows", (alice, carol), True, bob)
        assert len(core.query_scope("agent", BeliefFilter())) == 2
    finally:
        store.close()


def _snapshot(store: SQLiteEventStore) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: sorted(tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for table in ("belief_ids", "belief_entities")
    }
