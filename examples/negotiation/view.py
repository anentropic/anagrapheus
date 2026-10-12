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

So the guarantee is enforced rather than documented. A :class:`PartyView` reads through
one guarded function that **refuses** any scope outside this party's information set —
its own ``held`` and anybody's ``stated`` — raising :class:`ScopeNotVisible` rather than
answering. A peek becomes a loud failure instead of a plausible value.

This is the same move the chronicle made in note 05: :class:`~anagrapheus.chronicle.
reading.Anchors` and ``Prose`` are separate objects so that a binding read *cannot*
reach generated prose by accident. Both replace a rule someone must remember with a
shape that pushes back.

**The first attempt at this did not work, and the failure is worth keeping.** It bound
``self._query = table.core.query_scope`` and claimed the view "holds no reference to the
belief core at all". That is false: a bound method carries ``__self__``, so
``view._query(held("uk"), …)`` answered with another party's private beliefs and
``view._query.__self__`` was the entire ``MemoryCore`` — writes included. The test that
was supposed to prove the claim asserted ``not hasattr(view, "core")``, which passed
throughout. **Absence of an attribute is not absence of a path.** Note 06 §2c.

**And "unwritable" was the wrong word.** Python has no way to make a value unreachable:
this guard closes over ``query_scope``, and ``read.__closure__`` still finds it. What is
true, and all that is claimed now, is that every *ordinary* read goes through a check
that fails loudly on a scope this party may not see. Deliberate introspection defeats
it; a mistake does not.

**What this is not.** It is not security — a caller holding the ``Table`` can read
whatever it likes, and should be able to: the composition root is omniscient by
necessity, since somebody has to seed both sides. It is a constraint on *the reasoning
path*, which is where the modelling claim lives.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from doxastica import BeliefFilter
from examples.negotiation.scopes import held, stated

from anagrapheus.identity import IdentityRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from doxastica import BeliefState
    from examples.negotiation.table import Table

    from anagrapheus.timeline import DomainEvent

__all__ = ["PartyView", "ScopeNotVisible"]


class ScopeNotVisible(LookupError):
    """A party tried to read a scope outside its information set."""

    def __init__(self, scope: str, party: str) -> None:
        super().__init__(
            f"{party!r} may not read {scope!r}: a view reads its own `held` scope and "
            f"anybody's `stated` scope, and nothing else (R29). Reading another party's "
            f"private beliefs would yield the *true* answer rather than this party's "
            f"possibly-wrong model, and false belief would stop being representable."
        )
        self.scope = scope
        self.party = party


class PartyView:
    """
    One party's information set: what it may read while deciding what to do.

    Construct through :meth:`~examples.negotiation.table.Table.view`, which is the only
    place that knows how to wire one.
    """

    def __init__(self, table: Table, party: str) -> None:
        self.party = party
        self.predicates = table.predicates
        # Read-only *live* views, not snapshots. `dict(...)` froze the tables at
        # construction, so a `Negotiator` built before `Table.seat()` or
        # `table_clause()` raised `KeyError` for the new name and `others()` quietly
        # omitted the new party -- a stale view that still read live beliefs, which is
        # the worst of both. A negotiation that seats a party mid-round is ordinary.
        self.seats = MappingProxyType(table.seats)
        self.clauses = MappingProxyType(table.clauses)
        # A *guarded* reader, not `table.core.query_scope` bound. Aliasing the bound
        # method was the first attempt and it left the guarantee entirely open: a bound
        # method carries `__self__`, so the core -- reads and writes both -- came along
        # with it. See the module docstring.
        self.read = _guarded(table.core.query_scope, party)
        self._read = table.reader.read
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
        return self.read(held(self.party))

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
        return self.read(stated(party))

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
        """Every party but this one, as of now — not as of construction."""
        return tuple(p for p in self.seats if p != self.party)

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
        return self._about("position", party, clause)

    def red_line_id(self, party: str, clause: str) -> str:
        """
        The belief id for what this party has concluded is ``party``'s bottom line.

        The update call's output, and the one belief about another party that R28's
        exception lets a party *store*: a disposition read with no witness trail. Read
        back into the brief by :func:`~examples.negotiation.agent.brief_for` — which it
        was not, until note 06 §11.
        """
        return self._about("red_line", party, clause)

    def _about(self, predicate: str, party: str, clause: str) -> str:
        """A belief id about ``party`` and ``clause``, every reference canonicalized."""
        return self.predicates.belief_id(
            predicate,
            self._canonical(self.seats[party]),
            # The clause is canonicalized too. Clauses are identity entities as much as
            # parties are (`Table.table_clause` establishes one), so two names for one
            # clause can merge, and a lookup on the absorbed id would miss every
            # re-keyed belief -- silently, returning `unknown` in a brief. Note 06 §2a
            # was written about exactly this and then applied to parties only.
            self._canonical(self.clauses[clause]),
        )

    def witnessed_id(self, party: str, event_id: UUID) -> str:
        """The belief id for "``party`` witnessed this event"."""
        return self.predicates.belief_id("witnessed", self._canonical(self.seats[party]), event_id)


def _guarded(
    query: Callable[[str, BeliefFilter], Sequence[BeliefState]], party: str
) -> Callable[[str], list[BeliefState]]:
    """
    A reader that answers only for scopes ``party`` may see.

    Own ``held``, anybody's ``stated``, nothing else. `stated` needs no party list --
    those scopes are public by definition, and enumerating the seated parties here would
    only go stale the moment another is seated mid-negotiation.
    """

    def read(scope: str) -> list[BeliefState]:
        if scope != held(party) and not scope.endswith(":stated"):
            raise ScopeNotVisible(scope, party)
        return list(query(scope, BeliefFilter()))

    return read
