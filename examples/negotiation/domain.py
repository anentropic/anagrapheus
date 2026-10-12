"""
The negotiation's own vocabulary: its events, its entities, its predicates.

Deliberately the *application's*, not the library's — doc 00 §8's rule is that "the
stream's vocabulary belongs to whoever owns the domain", and this module is what that
looks like from the consumer's side.

The domain is a multilateral negotiation: several parties, a set of clauses, and a
record of who proposed what and who said what about it. It was chosen because §10 Q4
asks for a second consumer that resembles NVM as little as possible, and because it is
the smallest domain in which parties must model *each other* rather than only the world.

**A belief about access names an event by its envelope id, not by a minted entity.**
Parties are identity-layer entities because they can *merge* -- two names turning out
to be one negotiator is the whole of note 06 §4. Events cannot merge, so minting a
parallel entity for each one would add machinery no capability could ever use. The
envelope id is already a stable UUID naming exactly one event, and
`PredicateRegistry.belief_id` takes UUIDs rather than entities, so `witnessed(party,
event)` keys on it directly. A re-key never touches these: plans are computed from
identity decisions, and no decision ever names an event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, get_args
from uuid import UUID  # noqa: TC003 -- pydantic resolves event annotations at runtime

from anagrapheus.beliefs import Predicate, PredicateKind, PredicateRegistry
from anagrapheus.timeline import DomainEvent, EventKey

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import Any

    from examples.negotiation.nesting import Interpret

    from anagrapheus.timeline import EventTypeRegistry

__all__ = [
    "NAMESPACE",
    "PREDICATES",
    "ClauseTabled",
    "PositionStated",
    "RoundClosed",
    "SideRemarkOverheard",
    "interpreter",
    "predicates",
    "register",
]

#: This application's namespace. Never ``"anagrapheus"``, which the library reserves.
NAMESPACE = "negotiation"


class ClauseTabled(DomainEvent, frozen=True):
    """A clause enters the negotiation and becomes something to have a position on."""

    clause_id: UUID
    title: str


class PositionStated(DomainEvent, frozen=True):
    """
    A party says, in the room, what it wants on a clause.

    ``heard_by`` is who was in the room, and it is what makes a *modelled* mind able to
    model a third. Without it a derived mind knows what was said and nothing about who
    else knows it, so the tower stops at depth 1 over public events and only an
    overheard remark can carry the next rung — a limit of this event's shape rather than
    of the design. The same field `SideRemarkOverheard.overheard_by` is, for the case
    where the room is one person.

    It is still not a gate. A witness who learns of this event forms an *ordinary
    first-order belief* about who else was there (R27), and that belief may be wrong
    like any other; nothing here consults it to decide what anybody actually saw.
    """

    party: str
    clause_id: UUID
    position: str
    #: Who the event says was in the room. Defaults to nobody, which is honest for an
    #: event constructed without saying — an empty room carries no access.
    heard_by: tuple[str, ...] = ()


class SideRemarkOverheard(DomainEvent, frozen=True):
    """
    Something a party let slip that bears on what it *actually* wants.

    The domain's whole asymmetry in one event. A stated position is public and cheap
    to make; a remark not meant to be heard is worth more precisely because of that,
    and it is the only evidence here about what a party *actually* wants.

    It also carries **who overheard it**, which is what makes the domain strategic
    rather than merely informational. That field is not a gate — per R27 a party acts
    on its *belief* about who heard, which may be wrong, and a party mis-tracking that
    is a party that can be played. Two parties told about the same remark can still
    end up modelling the speaker differently, which is the case a single-holder design
    cannot express at all.
    """

    speaker: str
    overheard_by: str
    clause_id: UUID
    suggests: str


class RoundClosed(DomainEvent, frozen=True):
    """A round of negotiation ends. The chronicle's unit boundary."""

    round_number: int


#: How far a party will move on a clause: what an offer states, a remark lets slip, and
#: a bottom line concludes. ``concede`` gives way, ``hold_firm`` will not move yet
#: but has not ruled it out, ``never`` will not move at all.
type Disposition = Literal["concede", "hold_firm", "never"]

#: :data:`Disposition` as the set a predicate declares, so the type and the registry
#: cannot drift apart.
DISPOSITIONS: Final = frozenset[str](get_args(Disposition.__value__))


#: The negotiation's predicates. `position` and `red_line` are functional -- a party
# holds one position on a clause at a time, and revising supersedes correctly.
#
# `witnessed` is the other kind, and it is the one this domain turns on. §6a: a
# relational predicate must put *every* argument in the id and use a boolean as the
# value, or revising silently retracts. `witnessed(party, event)` does exactly that --
# both arguments in the id, `True`/`False` as the value, and the uncertainty carried by
# stance rather than smuggled into the value.
#
# **It is the engine, not bookkeeping.** NVM R27 (RATIFIED 2026-06-17) makes access
# first-class observable content: actors form *ordinary first-order* beliefs about who
# witnessed what, and those beliefs are **allowed to diverge from the true witness
# set** -- *"the divergence is the deception/surprise engine."* A party that
# mis-tracks who overheard a remark is a party that can be played, and everything the
# derived read in `nesting.py` can say about another mind is gated by these.
#
# **`red_line` answers in a declared vocabulary, and `position` does not.** A bottom line
# is a conclusion a model writes about another party, read back into the next brief and
# compared across scopes, so it is held to :data:`Disposition` on write. Until note 06
# §13 closed it the three words lived in the data and in the model's schema and nowhere
# the belief layer could see. A position stays open: the EU's own seeded want is
# `settle`, which is a goal rather than a disposition, and closing the predicate would
# have forced one vocabulary onto two different questions.
PREDICATES = (
    Predicate("position", 2, PredicateKind.FUNCTIONAL),
    Predicate("red_line", 2, PredicateKind.FUNCTIONAL, values=DISPOSITIONS),
    Predicate("witnessed", 2, PredicateKind.RELATIONAL),
)


def predicates() -> PredicateRegistry:
    """
    The registry every scope in this example mints through.

    §6c's divergence query works *only* if both scopes minted their ids through the
    same registry, which is the argument for there being exactly one of these.
    """
    return PredicateRegistry(PREDICATES, namespace=NAMESPACE)


def register(types: EventTypeRegistry) -> None:
    """Register this domain's events. The application's job, not the library's."""
    types.register(EventKey(NAMESPACE, "ClauseTabled", 1), ClauseTabled)
    types.register(EventKey(NAMESPACE, "PositionStated", 1), PositionStated)
    types.register(EventKey(NAMESPACE, "SideRemarkOverheard", 1), SideRemarkOverheard)
    types.register(EventKey(NAMESPACE, "RoundClosed", 1), RoundClosed)


def interpreter(
    registry: PredicateRegistry,
    seats: Mapping[str, UUID],
    canonical: Callable[[UUID], UUID],
) -> Interpret:
    """
    How this domain reads an event as the beliefs it would give a witness.

    Supplied to :func:`~examples.negotiation.nesting.reconstruct`, because §8 puts the
    vocabulary with whoever owns the domain: the library ships the shape, the
    application says what its events *mean*.

    Two events carry belief, and the asymmetry between them is the domain:

    - **`PositionStated`** — a witness learns what a party said, **and who else was in
      the room**. Public, and cheap to say, so it is only ever evidence about the
      party's *stated* position — but the room is what lets a modelled mind hold a model
      of a third, so a public statement carries the tower's next rung as much as a
      remark does.
    - **`SideRemarkOverheard`** — a witness learns what a party appears to *want*, which
      is worth more precisely because it was not meant to be heard.

    Both map to the same belief id, so a remark heard after a statement supersedes it in
    the fold. That is not a rule written anywhere: it falls out of later events winning,
    which is the only revision a derived view is allowed.

    **Both events name who else was there**, and a witness who learns that forms an
    ordinary access-belief about each of them (R27). Without it a modelled mind could
    never have a model of its own, and the tower would stop at depth 1 for want of the
    gate rather than by design.

    ``heard_by`` was added to `PositionStated` in M8b and needs no upcast, for one
    reason worth naming rather than assuming: **no `PositionStated` had ever been
    persisted**. M7 defined the event and never emitted it. Had any log held one, a
    defaulted field would still have deserialized, but the honest move would have been a
    version bump and an upcast that says what the old rooms are taken to have contained
    -- because "nobody was there" is a claim about those events, not an absence of one.

    **Every entity reference is canonicalized at read time -- clauses as much as
    parties.** Clauses are identity entities too (`Table.table_clause` establishes one),
    so two names for one clause can merge, and the immutable event keeps naming the
    absorbed id. Missing that on the clause leaves the derived model keyed differently
    from the re-keyed stored beliefs, so a comparison reports no overlap and a brief
    line reads `unknown` -- silently, and only after a merge. A merge
    re-keys stored beliefs, but it cannot reach into an event payload -- the log is
    immutable, and a party named in a remark stays named there forever. A *stored*
    model of another mind is carried along by the re-key; a *derived* one is rebuilt
    from events that still say `HMG` after HMG has been merged into the UK, and would
    key on an entity that no longer names anything. So every party reference goes
    through `canonical`, which follows the merge chain to the id that survives.

    That asymmetry is invisible while nested beliefs are stored, and it is a real cost
    of R28 rather than a free consequence: derived reads must resolve identity
    themselves. Note 06 records it.

    **What this deliberately does not do is consult the truth.** `overheard_by` is used
    here only to say what a *witness of this event* comes to believe about who else
    heard it. Nothing gates on it. Whether a party actually heard something is a fact;
    whether another party believes they heard it is a belief, may be wrong, and is the
    only thing the reconstruction is allowed to use.
    """

    def read(event_id: UUID, payload: DomainEvent) -> Sequence[tuple[str, Any]]:
        if isinstance(payload, PositionStated):
            return (
                (
                    registry.belief_id(
                        "position",
                        canonical(seats[payload.party]),
                        canonical(payload.clause_id),
                    ),
                    payload.position,
                ),
                *(
                    (
                        registry.belief_id("witnessed", canonical(seats[heard]), event_id),
                        True,
                    )
                    for heard in payload.heard_by
                ),
            )
        if isinstance(payload, SideRemarkOverheard):
            return (
                (
                    registry.belief_id(
                        "position",
                        canonical(seats[payload.speaker]),
                        canonical(payload.clause_id),
                    ),
                    payload.suggests,
                ),
                (
                    registry.belief_id(
                        "witnessed", canonical(seats[payload.overheard_by]), event_id
                    ),
                    True,
                ),
            )
        return ()

    return read
