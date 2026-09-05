"""
What one party can see — an information set, made structural.

NVM **R29 (RATIFIED 2026-06-17)** is the constraint that makes false belief possible:

> the reconstruction **reads only the observer's (A's) scope, never the target's
> (B's)** … If you peeked at B's actual scope you would get the *true* answer, not A's
> (possibly wrong) model, and false belief becomes unrepresentable.

Nothing in ``doxastica`` enforces that. ``MemoryCore.query_scope`` takes any string, so
``query_scope(held("uk"), ...)`` from inside the EU's reasoning is a one-line mistake
that returns a plausible answer and quietly destroys the thing the demo exists to show.
Worse, it destroys it *invisibly*: a peeking agent looks like a well-informed agent.

So the guarantee is built rather than documented. A :class:`PartyView` exposes its
party's own scope, the public log, and every other party's **stated** scope — and holds
no reference to the belief core at all, so there is nothing to reach through. Reading
another party's private beliefs is not "avoided by discipline"; it is unwritable.

This is the same move the chronicle made in note 05: :class:`~anagrapheus.chronicle.
reading.Anchors` and ``Prose`` are separate objects so that a binding read *cannot*
reach generated prose by accident. Both replace a rule someone must remember with a
shape that has no room for the mistake.

**What this is not.** It is not security — a caller holding the ``Table`` can read
whatever it likes, and should be able to: the composition root is omniscient by
necessity, since somebody has to seed both sides. It is a constraint on *the reasoning
path*, which is where the modelling claim lives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doxastica import BeliefFilter
from examples.negotiation.scopes import held, stated

from anagrapheus.identity import IdentityRegistry

if TYPE_CHECKING:
    from uuid import UUID

    from doxastica import BeliefState
    from examples.negotiation.table import Table

    from anagrapheus.timeline import DomainEvent

__all__ = ["PartyView"]


class PartyView:
    """
    One party's information set: what it may read while deciding what to do.

    Construct through :meth:`~examples.negotiation.table.Table.view`, which is the only
    place that knows how to wire one.
    """

    def __init__(self, table: Table, party: str) -> None:
        self.party = party
        self.predicates = table.predicates
        self.seats = dict(table.seats)
        self.clauses = dict(table.clauses)
        # Deliberately bound as methods of the table rather than keeping the table --
        # `self._table.core` would put the whole belief store one attribute away and
        # give the guarantee back. A closure over the two reads this view is allowed
        # leaves nothing to dereference.
        self._query = table.core.query_scope
        self._read = table.reader.read
        self._parties = table.parties
        #: How to read an event as beliefs, with identity already resolved. Bound
        #: here so a reconstruction from this view cannot be handed a different
        #: reading of the same events than the table's own queries use.
        self.interpret = table.interpret()
        #: Follow an entity's merge chain to the id that survives. Every belief id this
        #: view mints goes through it, because `seats` keeps the pre-merge names: after
        #: HMG merges into the UK, `seats["HMG"]` still returns an id that names nothing
        #: and every lookup made with it would miss in silence.
        self._canonical = IdentityRegistry(table.identity).canonical

    # ---- what this party knows -------------------------------------------------------

    def own(self) -> list[BeliefState]:
        """Everything this party holds. Its own scope, so all of it is legible."""
        return list(self._query(held(self.party), BeliefFilter()))

    def own_belief(self, belief_id: str) -> BeliefState | None:
        """One belief this party holds, by exact id, or ``None``."""
        return next((s for s in self.own() if s.belief_id == belief_id), None)

    def public(self, party: str) -> list[BeliefState]:
        """
        What ``party`` has said in the room. Public by construction.

        A party's own ``stated`` scope is readable here too, which is right: knowing
        what you have committed to publicly is part of your own information set, and is
        exactly what :meth:`~examples.negotiation.table.Table.own_gap` measures against.
        """
        return list(self._query(stated(party), BeliefFilter()))

    def public_belief(self, party: str, belief_id: str) -> BeliefState | None:
        """One thing ``party`` has said, by exact id, or ``None``."""
        return next((s for s in self.public(party) if s.belief_id == belief_id), None)

    def log(self, *, since_seq: int = 0) -> list[tuple[UUID, DomainEvent]]:
        """
        The event log, decoded: ``(event id, payload)`` in order.

        Through the :class:`~anagrapheus.timeline.EventReader` rather than the store, so
        payloads arrive upcast and validated into their models. Reading the store
        directly hands back raw dicts, every ``isinstance`` check against a domain event
        quietly fails, and a reconstruction returns an empty mind that looks exactly
        like an ignorant one. That cost an hour here.

        Shared by everyone — being *in* the log is not being *seen*.

        Access is a separate question, and a belief rather than a fact: what this party
        thinks each other party witnessed lives in its own scope as ``witnessed(...)``
        (R27), and may be wrong. Filtering the log by the true witness set here would
        hand every party the truth and make deception unrepresentable, which is the
        failure R29 exists to prevent, arriving one layer lower down.
        """
        return [(stored.envelope.event_id, decoded) for stored, decoded in self._read(since_seq)]

    def others(self) -> tuple[str, ...]:
        """Every party but this one."""
        return tuple(p for p in self._parties if p != self.party)

    def position_id(self, party: str, clause: str) -> str:
        """
        The belief id for ``party``'s position on ``clause``.

        By exact id, always. Taking the first belief in a scope reads correctly while
        each scope holds one thing and starts answering with an unrelated clause -- or
        with a ``red_line`` rather than a ``position`` -- the moment it holds two. An
        agent would then negotiate confidently about the wrong proposition, which is
        the kind of wrong that never raises. It has already happened once here; note 06
        §5b records it.
        """
        return self.predicates.belief_id(
            "position", self._canonical(self.seats[party]), self.clauses[clause]
        )

    def witnessed_id(self, party: str, event_id: UUID) -> str:
        """The belief id for "``party`` witnessed this event"."""
        return self.predicates.belief_id("witnessed", self._canonical(self.seats[party]), event_id)
