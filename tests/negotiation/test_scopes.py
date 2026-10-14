"""
Several holders, believing different things about each other.

The divergence query exists for *"the whole multi-agent-disagreement case"*.
Until this example the library's own suite only ever ran it as ``"agent"`` against
``"world"`` — one believer measured against ground truth, which is the degenerate case
of a design built for several. These are the tests that were missing.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from examples.negotiation import domain
from examples.negotiation.scopes import held, party_of, stated

if TYPE_CHECKING:
    from examples.negotiation.table import Table


def _positions(table: Table, clause: str = "fisheries") -> tuple[uuid.UUID, uuid.UUID]:
    """
    One clause, and the UK's three positions on it: wanted, said, and assumed.

    The first two are stored, first-order and the UK's own. **The third is not stored
    at all** — since M8 it is derived from what the EU believes the UK let slip, because
    nested belief is never stored. So the setup that used to be a third `table.believe`
    is now an event plus an access-belief, which is the whole difference that makes to a
    consumer.
    """
    fisheries = table.table_clause(clause)
    uk = table.seats["uk"]
    source = uuid.uuid7()
    table.believe(held("uk"), "position", (uk, fisheries), "concede", source)
    table.believe(stated("uk"), "position", (uk, fisheries), "never", source)

    remark = table.types.pending(
        domain.SideRemarkOverheard(
            speaker="uk", overheard_by="eu", clause_id=fisheries, suggests="hold_firm"
        )
    )
    table.append(remark)
    table.believe(held("eu"), "witnessed", (uk, remark.envelope.event_id), True, uuid.uuid7())
    table.projector.catch_up()
    return uk, fisheries


def test_three_questions_from_two_stored_scopes_and_one_derived_model(table: Table):
    """
    The point of the whole domain, in one assertion.

    One belief id, and a single comparison separating *being wrong about someone*,
    *their own gap between word and intent*, and *whether you believe what they said*.
    A design with one believer cannot state any of the three.

    Two of the three sides are stored scopes; the third is reconstructed per call and
    discarded. That the queries read the same afterwards is the point — the
    ruling changes where the model lives, not what can be asked of it.
    """
    _positions(table)

    misread = table.misreading("eu", "uk")
    gap = table.own_gap("uk")
    scepticism = table.taken_at_their_word("eu", "uk")

    assert [(d.held, d.reference) for d in misread] == [("hold_firm", "concede")]
    assert [(d.held, d.reference) for d in gap] == [("never", "concede")]
    assert [(d.held, d.reference) for d in scepticism] == [("hold_firm", "never")]


def test_the_own_gap_query_is_one_only_that_party_can_run(table: Table):
    """
    The asymmetry that makes the domain strategic rather than merely multi-party.

    Nobody but the UK holds ``party:uk:held``, so nobody else can measure the UK's
    distance between word and intent. The EU can measure its *own* error and its *own*
    scepticism, and no amount of either recovers the answer.
    """
    _positions(table)

    assert table.own_gap("uk"), "the party itself sees the gap"
    # The EU's two available queries both compare against something the EU holds.
    assert all(d.held == "hold_firm" for d in table.misreading("eu", "uk")), (
        "the EU can only ever measure from its own model outward"
    )


def test_a_belief_id_is_shared_across_scopes_deliberately(table: Table):
    """
    One proposition, one belief id, however many holders.

    Revision history keys on the belief id alone and is cross-scope, and for divergence
    queries that collision is the point.

    Worth asserting because it looks like a bug from the outside: three holders, one
    belief id. If the ids differed per scope, every divergence query would return
    nothing and report perfect agreement.
    """
    from doxastica import BeliefFilter

    _positions(table)

    stored = {
        state.belief_id
        for scope in (held("uk"), stated("uk"))
        for state in table.core.query_scope(scope, BeliefFilter())
    }
    derived = set(table.model_held_by("eu", "uk").beliefs)

    assert len(stored) == 1, "one proposition, one id, however many stored holders"
    assert stored <= derived, (
        "and the derived model mints through the same registry, which is what lets a "
        "reconstruction be compared against a stored scope at all -- if ids differed "
        "per holder every divergence query would return nothing and report agreement"
    )
    assert len(derived) > len(stored), (
        "the derived mind holds more than the proposition asked about: the remark "
        "named who overheard it, so a witness also forms an access-belief about that "
        "third party (R27). That is what lets the next rung of the tower gate at all."
    )


def test_a_scope_that_never_held_the_belief_is_absent_not_disagreeing(table: Table):
    """An absence is not a contradiction — the distinction `divergence` draws."""
    _positions(table)

    # France was never seated and holds nothing.
    assert table.misreading("eu", "eu") == []


def test_scope_names_round_trip_to_their_party(table: Table):
    """`party_of` is what makes a re-key able to report which holder it touched."""
    assert party_of(held("uk")) == "uk"
    assert party_of(stated("eu")) == "eu"
