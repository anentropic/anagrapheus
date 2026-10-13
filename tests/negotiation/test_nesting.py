"""
Higher-order belief, derived and never stored — the rules for it asserted as behaviour.

The M7 demo stored ``party:{a}:model-of:{b}`` and called it higher-order belief. NVM
rules that out — *"no `BeliefState` ever stores 'X believes Y believes …'"* — because a
stored model is folded forward by the same world events that move first-order belief,
and a false belief about someone else's belief stops being representable.

The load-bearing test here is :func:`test_a_false_belief_survives_the_world_moving`,
because it is the one the stored design **could not pass**. Everything else guards the
constraints that make it possible.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from doxastica import BeliefFilter
from examples.negotiation import domain
from examples.negotiation.nesting import DEFAULT_DEPTH, belief_of, reconstruct
from examples.negotiation.scopes import held, stated
from examples.negotiation.view import ScopeNotVisible

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from examples.negotiation.table import Table


def _interpret(table: Table):
    return table.interpret()


def _saw(table: Table, *, believer: str, party: str, event: UUID, it_happened: bool = True) -> None:
    """``believer`` comes to believe ``party`` witnessed ``event``. May be wrong."""
    table.believe(
        held(believer), "witnessed", (table.seats[party], event), it_happened, uuid.uuid7()
    )


def _remark(table: Table, *, speaker: str, overheard_by: str, clause: UUID, suggests: str) -> UUID:
    event = table.types.pending(
        domain.SideRemarkOverheard(
            speaker=speaker, overheard_by=overheard_by, clause_id=clause, suggests=suggests
        )
    )
    table.append(event)
    return event.envelope.event_id


def _stated(table: Table, *, party: str, clause: UUID, position: str) -> UUID:
    event = table.types.pending(
        domain.PositionStated(party=party, clause_id=clause, position=position)
    )
    table.append(event)
    return event.envelope.event_id


# -- the ruling itself ------------------------------------------------------------------


def test_deriving_a_model_writes_nothing(table: Table):
    """
    Nested belief is never stored, made mechanical rather than trusted.

    A reading of the code is not evidence that it does not write: the whole failure mode
    is a write that looks harmless. So the core's three mutating operations are wrapped
    and the derivation is asserted to touch none of them.
    """
    fisheries = table.table_clause("fisheries")
    remark = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    _saw(table, believer="eu", party="uk", event=remark)
    table.projector.catch_up()

    writes: list[str] = []

    def recording(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            writes.append(name)
            return original(*args, **kwargs)

        return wrapper

    for op in ("revise", "expand", "contract", "add_edge"):
        setattr(table.core, op, recording(op, getattr(table.core, op)))

    reconstruct(table.view("eu"), ("uk",), interpret=_interpret(table))

    assert writes == [], "a derived model is built from reads only (R28, R50)"


def test_a_view_cannot_reach_another_partys_private_scope(table: Table):
    """
    A party models another only from its own beliefs, enforced rather than conventional.

    *"If you peeked at B's actual scope you would get the true answer, not A's
    (possibly wrong) model, and false belief becomes unrepresentable."*

    What is asserted, and it is narrower than an earlier version of this docstring
    claimed: the guarded reader **raises** on a scope outside the party's information
    set, and no attribute of the view is a bound method of the core. Not that the core
    is unreachable — it is, through `read.__closure__`, and Python offers no way to
    prevent that. Ordinary reads are checked; that is the claim.
    """
    fisheries = table.table_clause("fisheries")
    source = uuid.uuid7()
    table.believe(held("eu"), "position", (table.seats["eu"], fisheries), "settle", source)
    table.believe(held("uk"), "position", (table.seats["uk"], fisheries), "concede", source)
    table.believe(stated("uk"), "position", (table.seats["uk"], fisheries), "never", source)
    table.projector.catch_up()

    view = table.view("eu")

    # What the EU can read: its own beliefs, and what the UK has said publicly.
    assert [s.value for s in view.own()] == ["settle"]
    assert [s.value for s in view.public("uk")] == ["never"]

    # What it cannot. The UK privately wants to concede -- the table can see that, which
    # is what makes the next assertion worth making rather than vacuous.
    assert table.core.query_scope(held("uk"), BeliefFilter())[0].value == "concede"
    assert "concede" not in [s.value for s in view.own()]

    # The reader **refuses** a scope outside this party's information set. An earlier
    # version asserted only `not hasattr(view, "core")`, which passed while `_query`
    # was `core.query_scope` bound -- so `view._query(held("uk"), ...)` answered, and
    # `view._query.__self__` was the whole MemoryCore. Absence of an attribute is not
    # absence of a path; see note 06 §2c.
    with pytest.raises(ScopeNotVisible, match="party:uk:held"):
        view.read(held("uk"))

    # And the one route that made writes reachable is gone: nothing this view holds is
    # a bound method of the core.
    assert not any(getattr(value, "__self__", None) is table.core for value in vars(view).values())


def test_a_view_sees_parties_and_clauses_seated_after_it_was_built(table: Table):
    """
    A view reads live tables, not a snapshot of them.

    The first version took `dict(table.seats)` and `dict(table.clauses)` at
    construction. Belief and log reads stayed live, so a `Negotiator` built before a
    party was seated became *partly* stale — `others()` quietly omitted the newcomer and
    `position_id` raised `KeyError` for a name the table knew perfectly well. Review
    round 2. A negotiation that seats a party mid-round is ordinary, and the guarded
    reader already accounts for it by allowing any `stated` scope without a party list.
    """
    view = table.view("eu")

    table.seat("norway")
    quotas = table.table_clause("quotas")

    assert "norway" in view.others(), "seated after construction, and visible"
    assert view.position_id("norway", "quotas") == table.predicates.belief_id(
        "position", table.seats["norway"], quotas
    )


# -- the case the stored design could not represent --------------------------------------


def test_a_false_belief_survives_the_world_moving(table: Table):
    """
    The guard-and-courier case, translated — **the assertion M7 could not make**.

    The guard watches a courier stash a letter, watches the courier *leave*, then
    watches a thief move it. The guard's own belief updates on the move; the guard's
    belief about the **courier's** belief must specifically not, because the guard saw
    the courier go first.

    Here: the EU overhears the UK say it will concede, and believes the UK's delegate
    was not present when a later remark reversed that. So the EU's own view of the UK
    moves on the second remark and its **model of what the UK thinks it revealed** does
    not.

    A stored `model-of` row would be folded forward by the same world event that moved
    the first-order belief — *"and deception/surprise silently break."* Nothing stores
    this, so nothing folds it, and the stale model stays stale because staleness is the
    correct answer.
    """
    fisheries = table.table_clause("fisheries")

    # Round one: a remark the EU believes the UK's own delegate overheard.
    first = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    _saw(table, believer="eu", party="uk", event=first)

    # Round two: a reversal the EU hears and believes the UK delegate had already left for.
    second = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="never")
    _saw(table, believer="eu", party="uk", event=second, it_happened=False)
    table.believe(held("eu"), "witnessed", (table.seats["eu"], second), True, uuid.uuid7())
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)
    view = table.view("eu")

    # The world moved, and the EU's own reading of the UK moved with it...
    own_model = reconstruct(view, ("uk",), interpret=_interpret(table))
    assert own_model.get(position) == "concede", (
        "the EU's model of the UK is gated by what the EU thinks the UK witnessed, "
        "and it thinks the UK missed the reversal -- so the model stays on the first "
        "remark even though a later event contradicted it. A stored row would have "
        "folded forward here and quietly agreed with the world."
    )
    assert second not in own_model.over, "the reversal was gated out, not merely outvoted"


def test_the_gate_is_belief_not_truth(table: Table):
    """
    Access-beliefs are *"allowed to diverge from the true witness set"*.

    The event records who really overheard it. Nothing consults that field to decide
    what a party may model — if it did, every party would be handed the truth and
    deception would stop existing. Same events, two parties, two different models,
    because they believe different things about who heard what.
    """
    fisheries = table.table_clause("fisheries")
    remark = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    # Both parties saw the remark itself; they disagree about whether the UK knows it.
    _saw(table, believer="eu", party="uk", event=remark, it_happened=True)
    _saw(table, believer="uk", party="uk", event=remark, it_happened=False)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)

    assert belief_of(table.view("eu"), ("uk",), position, interpret=_interpret(table)) == "concede"
    assert belief_of(table.view("uk"), ("uk",), position, interpret=_interpret(table)) is None


def test_ignorance_makes_an_empty_model_not_a_wrong_one(table: Table):
    """
    Ignorance makes an empty model, not a wrong one.

    *"Where A is ignorant, A's model of B is correspondingly empty/uncertain — which is
    correct."* A party that holds no access-belief about another models nothing about
    it. That is an absence, not a claim — the same distinction `divergence` draws, one
    level up.
    """
    fisheries = table.table_clause("fisheries")
    _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    table.projector.catch_up()

    model = reconstruct(table.view("eu"), ("uk",), interpret=_interpret(table))

    assert model.beliefs == {}
    assert model.over == ()


# -- the tower ---------------------------------------------------------------------------


def test_second_order_is_not_reducible_to_first(table: Table):
    """
    A party can be right about the world and wrong about another's belief.

    **The first version of this test proved the opposite of its name.** It used a side
    remark, which names who overheard it — so the modelled UK was handed an access-belief
    about the EU, both levels answered `"concede"`, and the assertion recorded that they
    *agree*. Deleting the recursion entirely passed all eleven tests. Review round 2.

    The levels have to be able to disagree, so the evidence is a `PositionStated`, which
    carries no access-belief at all:

    - **depth 1** — the EU believes the UK witnessed its own statement, so the EU's model
      of the UK holds that position.
    - **depth 2** — what the EU thinks the UK thinks *the EU* witnessed. The gate at that
      level reads the level-1 model, which has no `witnessed(eu, …)` in it, so the EU's
      model of the UK's model of the EU is empty.

    Right about the world, wrong (or blank) about another's belief, and the error lives
    at a level no first-order record can express — which is why the literature builds
    the *second*-order Sally-Anne task. A recursion that collapsed the levels would make
    these two equal, so this now fails against that mutant.
    """
    fisheries = table.table_clause("fisheries")
    said = _stated(table, party="uk", clause=fisheries, position="never")
    _saw(table, believer="eu", party="uk", event=said, it_happened=True)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)
    view = table.view("eu")
    interpret = _interpret(table)

    first = belief_of(view, ("uk",), position, interpret=interpret)
    second = belief_of(view, ("uk", "eu"), position, interpret=interpret)

    assert first == "never", "the EU knows what the UK said, and thinks the UK knows it"
    assert second is None, (
        "but nothing told the modelled UK that the EU was there, so the EU's model of "
        "the UK's model of the EU is empty -- a level the first-order answer cannot "
        "express, and the whole point of the tower"
    )
    assert first != second, "the two levels genuinely disagree"


def test_a_remark_carries_access_so_the_second_level_can_fill(table: Table):
    """
    The complement, and why the level-2 blank above is a gate and not a dead end.

    A `SideRemarkOverheard` names who overheard it, so a witness forms an access-belief
    about that third party — and *that* is what lets the next rung gate at all.
    Same shape as the test above, one event type different, and the second level fills.
    """
    fisheries = table.table_clause("fisheries")
    leak = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    _saw(table, believer="eu", party="uk", event=leak, it_happened=True)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)
    view, interpret = table.view("eu"), _interpret(table)

    assert belief_of(view, ("uk",), position, interpret=interpret) == "concede"
    assert belief_of(view, ("uk", "eu"), position, interpret=interpret) == "concede", (
        "the remark told the modelled UK that the EU overheard, so the level-2 gate admits it too"
    )


def test_depth_bottoms_out_at_the_cap(table: Table):
    """
    Depth is capped at 3, raisable per query — as a limit on realisation.

    A tuning knob, not an architectural commitment — so the cap must be a *limit on
    realisation*, not on what can be asked. A truncated model says so rather than
    reporting an empty mind, which would be indistinguishable from a mind that believes
    nothing.
    """
    fisheries = table.table_clause("fisheries")
    remark = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    _saw(table, believer="eu", party="uk", event=remark)
    table.projector.catch_up()

    deep = ("uk", "eu", "uk", "eu")
    assert len(deep) > DEFAULT_DEPTH

    capped = reconstruct(table.view("eu"), deep, interpret=_interpret(table))
    assert capped.truncated is True
    assert capped.beliefs == {}

    raised = reconstruct(table.view("eu"), deep, interpret=_interpret(table), depth=4)
    assert raised.truncated is False, "the cap is raisable per query, not a hard ceiling"


# -- U24: nothing caches ------------------------------------------------------------------


def test_no_cache_reintroduces_the_staleness_the_no_store_rule_dissolves(table: Table):
    """
    Nothing about a derived model is cached: worth an assertion rather than a promise.

    Because nested beliefs are never stored there is nothing to invalidate when access
    changes — *"the next derivation simply sees the reified access-belief."* A memo
    would quietly bring back the staleness the no-store discipline exists to dissolve.
    """
    fisheries = table.table_clause("fisheries")
    remark = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    _saw(table, believer="eu", party="uk", event=remark, it_happened=False)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)
    view, interpret = table.view("eu"), _interpret(table)

    assert belief_of(view, ("uk",), position, interpret=interpret) is None

    # The access-belief is revised; the very next derivation must see it.
    _saw(table, believer="eu", party="uk", event=remark, it_happened=True)
    table.projector.catch_up()

    assert belief_of(view, ("uk",), position, interpret=interpret) == "concede"


@pytest.mark.parametrize("chain", [("uk",), ("uk", "eu")])
def test_a_derived_model_is_not_a_belief_state(table: Table, chain: tuple[str, ...]):
    """
    It cannot be handed back to ``doxastica`` even by accident.

    The hazard is a stored nested record. The cheapest guard against storing one is a
    return type that no write on the core will accept, so the mistake fails at the call
    rather than succeeding quietly.
    """
    derived = reconstruct(table.view("eu"), chain, interpret=_interpret(table))

    assert not hasattr(derived, "belief_id")
    assert not hasattr(derived, "scope_id")
    assert derived.chain == chain


def test_a_remark_supersedes_a_statement_in_the_fold(table: Table):
    """
    The domain's asymmetry, and why it needs no rule of its own.

    A stated position and an overheard remark map to the *same* belief id, so the later
    one wins. That is the only revision a derived view is allowed: the design keeps the
    core's formal operator out of a modelled mind, because resolving a contradiction
    properly would need A's model of B's *entrenchment* — a portrait-level prose hint,
    not the total preorder the operator requires — and *"a model of another mind is
    deliberately best-effort."*

    Order is what carries the meaning, so the reverse order is asserted too: nothing
    here privileges remarks, and a statement made *after* a leak supersedes it.
    """
    fisheries = table.table_clause("fisheries")
    said = _stated(table, party="uk", clause=fisheries, position="never")
    leaked = _remark(table, speaker="uk", overheard_by="eu", clause=fisheries, suggests="concede")
    for event in (said, leaked):
        _saw(table, believer="eu", party="uk", event=event)
    table.projector.catch_up()

    position = table.predicates.belief_id("position", table.seats["uk"], fisheries)
    model = reconstruct(table.view("eu"), ("uk",), interpret=_interpret(table))

    assert model.get(position) == "concede", "the later event wins"
    assert model.over == (said, leaked), "and both were admitted -- superseded, not gated"
