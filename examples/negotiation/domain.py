"""
The negotiation's own vocabulary: its events, its entities, its predicates.

Deliberately the *application's*, not the library's — doc 00 §8's rule is that "the
stream's vocabulary belongs to whoever owns the domain", and this module is what that
looks like from the consumer's side.

The domain is a multilateral negotiation: several parties, a set of clauses, and a
record of who proposed what and who said what about it. It was chosen because §10 Q4
asks for a second consumer that resembles NVM as little as possible, and because it is
the smallest domain in which parties must model *each other* rather than only the world.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 -- pydantic resolves event annotations at runtime

from anagrapheus.beliefs import Predicate, PredicateKind, PredicateRegistry
from anagrapheus.timeline import DomainEvent, EventKey

if TYPE_CHECKING:
    from anagrapheus.timeline import EventTypeRegistry

__all__ = [
    "NAMESPACE",
    "PREDICATES",
    "ClauseTabled",
    "PositionStated",
    "RoundClosed",
    "SideRemarkOverheard",
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
    """A party says, in the room, what it wants on a clause."""

    party: str
    clause_id: UUID
    position: str


class SideRemarkOverheard(DomainEvent, frozen=True):
    """
    Something a party let slip that bears on what it *actually* wants.

    The domain's whole asymmetry in one event: a stated position updates a public
    scope, a side remark updates somebody's **model of** another party. Two parties
    overhearing the same remark can still end up with different models of the speaker,
    which is the case a single-holder design cannot express at all.
    """

    speaker: str
    overheard_by: str
    clause_id: UUID
    suggests: str


class RoundClosed(DomainEvent, frozen=True):
    """A round of negotiation ends. The chronicle's unit boundary."""

    round_number: int


#: The negotiation's predicates. `position` is functional -- a party holds one position
# on a clause at a time, and revising supersedes correctly. §6a's trap is the other
# kind: a relational predicate must put every argument in the id, or revising silently
# retracts. Neither predicate here is relational, and that is worth stating rather than
# leaving to be inferred from their absence.
PREDICATES = (
    Predicate("position", 2, PredicateKind.FUNCTIONAL),
    Predicate("red_line", 2, PredicateKind.FUNCTIONAL),
)


def predicates() -> PredicateRegistry:
    """The registry every scope in this example mints through.

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
