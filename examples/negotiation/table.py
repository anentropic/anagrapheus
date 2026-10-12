"""
The negotiating table: everything wired together, and the queries it can answer.

This is the composition root a real consumer would write — one store, one registry,
one predicate registry, and one belief fold that every scope's beliefs are folded by.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance
from examples.negotiation import domain
from examples.negotiation.nesting import DEFAULT_DEPTH, Derived, Interpret, reconstruct
from examples.negotiation.scopes import held, stated
from examples.negotiation.view import PartyView

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    FOLD_NAME,
    BeliefEvents,
    BeliefFold,
    Disagreement,
    Edge,
    SQLiteBeliefIndexStore,
    StateRef,
    divergence,
)
from anagrapheus.chronicle import (
    Brief,
    ChronicleFold,
    EventTypes,
    SQLiteChronicleStore,
)
from anagrapheus.identity import (
    IdentityFold,
    IdentityRegistry,
    SQLiteIdentityStore,
    establish,
)
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from uuid import UUID

    from doxastica import BeliefState

    from anagrapheus.beliefs import PredicateRegistry
    from anagrapheus.chronicle import Summarizer
    from anagrapheus.timeline import PendingEvent
    from anagrapheus.timeline.folds import ProjectionFold

__all__ = ["ROUND_BOUNDARY", "Table"]

#: What closes a chronicle unit here. `RoundClosed` was defined in M7 and never emitted;
#: naming it as the boundary is what makes it load-bearing rather than decorative. The
#: chronicle's unit vocabulary is the application's (note 05), and a negotiation's unit
#: is a round -- not a turn, not a session, and not something the library could guess.
ROUND_BOUNDARY = EventTypes((domain.NAMESPACE, "RoundClosed"))


@dataclass
class Table:
    """A negotiation in progress: the log, the belief store, and who is at it."""

    store: SQLiteEventStore
    reader: EventReader
    types: EventTypeRegistry
    #: Folds every belief event into doxastica and the entity index. The belief store
    #: is the fold's, not the table's: see :attr:`core`.
    beliefs: BeliefFold
    predicates: PredicateRegistry
    identity: SQLiteIdentityStore
    projector: Projector
    #: The pure folds alone -- identity and beliefs -- run on every :meth:`append`, so a
    #: belief is readable the moment its event is in the log. The chronicle is not in
    #: it: it spends a model call per round and runs when :attr:`projector` is asked to.
    #: Positions are per fold name, so the two projectors never re-fold each other's work.
    settle: Projector
    index_store: SQLiteBeliefIndexStore
    #: The chronicle's sections. Always present, because a store costs nothing; the
    #: *fold* is opt-in, because it spends a model call per round.
    chronicle: SQLiteChronicleStore
    #: Party name -> entity id. Parties are **identity-layer entities**, not strings:
    #: a belief names entities by id, so a party that can be merged must have one.
    #: Discovered by trying it the other way -- see note 06.
    seats: dict[str, UUID] = field(default_factory=dict)
    clauses: dict[str, UUID] = field(default_factory=dict)
    #: Clause title -> the ``ClauseTabled`` event that put it on the table. A belief
    #: about a clause that nothing else caused cites this, so it has a real event as its
    #: source rather than a bare `uuid7()` naming nothing -- which matters as soon as
    #: `get_scope_at` cuts a scope by source event and a belief citing a phantom lands
    #: in an order nobody chose.
    tabled_at: dict[str, UUID] = field(default_factory=dict)
    #: Called after every :meth:`append` with the events and the new last ``seq``.
    #: For watching a negotiation as it happens -- the UI snapshots the table here.
    #: A listener reads; one that wrote would re-enter the append it is reporting.
    on_append: list[Callable[[tuple[PendingEvent, ...], int], None]] = field(default_factory=list)

    @classmethod
    def open(cls, path: Path, *parties: str, summarize: Summarizer | None = None) -> Table:
        """
        Stand up a table over ``path``, establishing each party as an entity.

        ``summarize`` installs the chronicle fold, and is the one piece of wiring here
        that is deliberately optional. Every other fold is pure and free, so leaving it
        out could only produce a half-built table — the mistake note 06 §5a records.
        The chronicle is not free: it is generative, and installing it spends a model
        call on every ``RoundClosed``. A table opened to ask belief questions should not
        be billed for prose nobody reads, so the *store* is always there and the *fold*
        is asked for.
        """
        types = EventTypeRegistry()
        register_builtin_events(types)
        domain.register(types)
        store = SQLiteEventStore(path)
        identity = SQLiteIdentityStore(store.connection)
        index = SQLiteBeliefIndexStore(store.connection)
        chronicle = SQLiteChronicleStore(store.connection)
        reader = EventReader(store, UpcastRegistry(), types)
        predicates = domain.predicates()
        beliefs = BeliefFold(MemoryCore(InMemoryBackend()), predicates, index, types)
        # Both pure folds, always. A table without the belief fold is one with no
        # beliefs at all, and one without identity is one where `plan_rekeys` cannot
        # follow a merge. Leaving either to the caller meant every entry point
        # re-assembled this.
        pure: list[ProjectionFold] = [IdentityFold(identity, types), beliefs]
        folds = list(pure)
        if summarize is not None:
            # Last, and that ordering is not cosmetic: a summarizer reads the events it
            # is consolidating, and a round's beliefs should be folded before its prose
            # claims to describe them.
            folds.append(
                ChronicleFold(chronicle, reader, boundary=ROUND_BOUNDARY, summarize=summarize)
            )
        table = cls(
            store=store,
            reader=reader,
            types=types,
            beliefs=beliefs,
            predicates=predicates,
            identity=identity,
            index_store=index,
            chronicle=chronicle,
            projector=Projector(store, reader, folds),
            settle=Projector(store, reader, pure),
        )
        if store.read_position(FOLD_NAME):
            # The position is in the file and the beliefs are in memory, so a reopened
            # table would resume above beliefs it no longer holds. Fold them again.
            table.rebuild_beliefs()
        for name in parties:
            table.seat(name)
        return table

    def seat(self, name: str) -> UUID:
        """Establish a party as an entity and seat it. Returns its id."""
        entity_id, event = establish(self.types, name, provenance="seated")
        self.append(event)
        self.seats[name] = entity_id
        return entity_id

    def table_clause(self, title: str) -> UUID:
        """Establish a clause as an entity, so positions on it can be re-keyed too."""
        clause_id, event = establish(self.types, title, provenance="tabled")
        self.append(event)
        tabled = self.types.pending(domain.ClauseTabled(clause_id=clause_id, title=title))
        self.append(tabled)
        self.clauses[title] = clause_id
        self.tabled_at[title] = tabled.envelope.event_id
        return clause_id

    @property
    def parties(self) -> tuple[str, ...]:
        """The seated parties, by name."""
        return tuple(self.seats)

    # ---- writing ---------------------------------------------------------------------

    @property
    def core(self) -> MemoryCore:
        """
        The belief store, as the fold last left it.

        A property, because :meth:`rebuild_beliefs` replaces it: a reference taken
        before a rebuild reads the old store, which is still a valid fold of the same
        log and is simply no longer the one being written.
        """
        store = self.beliefs.store
        if not isinstance(store, MemoryCore):
            raise TypeError(f"the table folds into a MemoryCore, not {type(store).__name__}")
        return store

    @property
    def events(self) -> BeliefEvents:
        """Belief events minted through this table's one registry."""
        return BeliefEvents(self.types, self.predicates)

    def append(self, *events: PendingEvent) -> int:
        """
        Append, fold the pure projections, then tell the listeners.

        Folding before the listeners hear about it is the order that makes the UI's
        snapshot honest: a listener that read the beliefs straight after a belief event
        would otherwise see the table as it was one event ago.
        """
        last = self.store.append(events)
        self.settle.catch_up()
        for listener in self.on_append:
            listener(events, last)
        return last

    def believe(
        self,
        scope: str,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: object,
        cause: UUID,
        *,
        stance: Stance = Stance.certain,
        rests_on: BeliefState | None = None,
    ) -> BeliefState:
        """
        Hold a belief in ``scope``: append the event that says so, and return what it folded to.

        **The table never writes the belief store.** It appends a ``BeliefRevised``,
        and the belief exists because :class:`~anagrapheus.beliefs.BeliefFold` folded
        it, so dropping the store and replaying the log brings it back. Until note 03
        §9 this wrote straight into doxastica and appended a ``BeliefMinted`` beside
        it, and the value, the stance and the holder were in the one place nothing
        could rebuild.

        ``cause`` is the event the belief comes from, recorded as the envelope's
        ``causation_id``. The state's own ``source_event_id`` is the belief event, which
        is what lets the fold recognise a replay and what keeps doxastica's as-of order
        the log's order.

        ``rests_on`` records what the belief was derived from, as an edge carried *in
        the event*. An edge laid on the store directly would be one more thing the log
        does not hold, and a rebuilt store would quietly stop reporting that retracting
        the premise compromised the conclusion.

        The state is returned because a caller that wants to say *why* it holds a later
        belief needs it as that belief's ``rests_on``.
        """
        event = self.events.revise(
            scope,
            predicate,
            entity_ids,
            value,
            causation_id=cause,
            stance=stance,
            edges=() if rests_on is None else (Edge(StateRef.of(rests_on)),),
        )
        self.append(event)
        state = self.beliefs.state(
            StateRef(
                scope,
                self.predicates.belief_id(predicate, *entity_ids),
                event.envelope.event_id,
            )
        )
        if state is None:
            raise RuntimeError(f"{predicate} in {scope!r} was appended and did not fold")
        return state

    def rebuild_beliefs(self) -> int:
        """
        Drop the belief store and the index, and fold both again from the log.

        Note 03 §9's whole claim as an operation: nothing a party believes lives
        anywhere the log cannot reproduce. Returns the fold's new position.
        """
        fresh = MemoryCore(InMemoryBackend())
        return self.projector.rebuild(
            FOLD_NAME, reset=lambda: self.beliefs.reset(fresh), reset_in_transaction=True
        )

    # ---- the three questions ---------------------------------------------------------
    #
    # Asked from the *table*, which is omniscient by necessity: somebody has to seed both
    # sides and somebody has to score the result. A party could never compute
    # `misreading` about itself -- that is the whole point of it -- so these are the
    # analyst's queries, not an agent's. An agent sees only its `PartyView`.

    def misreading(
        self, holder: str, other: str, *, depth: int = DEFAULT_DEPTH
    ) -> list[Disagreement]:
        """
        How wrong ``holder`` is about ``other`` — the game-theoretic quantity.

        The left-hand side is **derived, not stored** (R28): what ``holder`` would
        conclude about ``other`` from the events it believes ``other`` witnessed. The
        right-hand side is ``other``'s real private scope, which only the table can see.

        M7 computed this as `divergence` between two stored scopes. That version could
        not go wrong in the way this one can, which sounds like a virtue and is the
        opposite: a stored model folds forward on world events, so it silently converged
        on the truth and the misreading it was built to measure quietly went to zero.
        """
        return _disagreements(self.model_held_by(holder, other, depth=depth), held(other), self)

    def own_gap(self, party: str) -> list[Disagreement]:
        """
        The distance between what ``party`` says and what it wants.

        Both sides are stored, first-order and real, so this stays an ordinary
        :func:`~anagrapheus.beliefs.divergence` — the M8 correction does not touch it.

        **Nobody but that party can ask this**, because nobody else has its ``held``
        scope. That asymmetry is what makes the domain strategic rather than merely
        multi-party, and it is not expressible at all in a design with one believer.
        """
        return divergence(self.core, stated(party), held(party))

    def taken_at_their_word(
        self, holder: str, other: str, *, depth: int = DEFAULT_DEPTH
    ) -> list[Disagreement]:
        """
        Where ``holder``'s model of ``other`` departs from what ``other`` has said.

        Distinct from :meth:`misreading`: this measures scepticism, not error. A holder
        can be right to disbelieve a stated position, and this query does not know which.
        """
        return _disagreements(self.model_held_by(holder, other, depth=depth), stated(other), self)

    def model_held_by(self, holder: str, target: str, *, depth: int = DEFAULT_DEPTH) -> Derived:
        """
        Reconstruct ``holder``'s model of ``target``. Ephemeral — never stored.

        Goes through the holder's own :class:`~examples.negotiation.view.PartyView`, so
        R29 holds here too: the table may be omniscient, but the reconstruction it asks
        for is still built from what the holder alone can see.
        """
        return reconstruct(
            self.view(holder),
            (target,),
            interpret=self.interpret(),
            depth=depth,
        )

    @property
    def canonical(self) -> Callable[[UUID], UUID]:
        """
        Follow an entity's merge chain to the id that survives.

        A property rather than a stored callable, for the reason :meth:`interpret` is
        rebuilt each call: the chain moves under a merge, and a closure captured at
        construction would answer with the pre-merge id exactly when a merge made the
        difference matter.
        """
        return IdentityRegistry(self.identity).canonical

    def interpret(self) -> Interpret:
        """
        How this table reads its events as beliefs, with identity resolved.

        Built fresh rather than cached: `seats` grows as parties are seated and the
        merge chain moves under it, so a closure captured once would go stale exactly
        when a merge made it matter.
        """
        return domain.interpreter(
            self.predicates, self.seats, IdentityRegistry(self.identity).canonical
        )

    # ---- the story so far --------------------------------------------------------------

    def brief(self) -> Brief:
        """
        The recency/depth split over this negotiation: the tail, plus consolidated rounds.

        A fresh object each call, deliberately. The chronicle grows as rounds close, and
        a `Brief` cached at construction would hand round four the summary of round one
        — the same staleness the view fixed for `seats` (note 06 §2d).

        Answers whether or not the chronicle fold was installed: with no fold there are
        no sections and the tail covers everything, which is exactly the degradation
        note 05 designed for. Consolidation lag is invisible by construction, so *no*
        consolidation is only the limiting case of a chronicle running behind.
        """
        return Brief(self.chronicle, self.reader)

    # ---- what each party may see -------------------------------------------------------

    def view(self, party: str) -> PartyView:
        """One party's information set — the only thing an agent is ever handed."""
        return PartyView(self, party)

    # ---- scope enumeration -----------------------------------------------------------

    def scopes(self) -> list[str]:
        """
        Every scope at this table.

        Derived from the parties, **not** from the belief store, which cannot enumerate
        scopes — a limitation that matters as soon as a re-key has to visit all of them.
        See :mod:`examples.negotiation.rekeying`.

        Two per party since M8, not three. The `model-of` scope is gone: what a party
        believes another holds is derived on demand and never written, so there is
        nothing for a re-key to visit. That is a real consequence of R28 rather than a
        tidy-up — a stored model of another mind would have to be re-keyed like anything
        else, and now cannot fall out of step because it does not persist.
        """
        return [f(p) for p in self.parties for f in (held, stated)]

    def close(self) -> None:
        self.store.close()


def _disagreements(model: Derived, reference: str, table: Table) -> list[Disagreement]:
    """
    Compare a *derived* model against a real scope.

    :func:`~anagrapheus.beliefs.divergence` takes two scope ids and cannot be used here:
    since R28 one side of these queries has no scope to name. It is a dict that existed
    for the duration of a call.

    The semantics are deliberately identical to the library's, and for the library's
    reason: **only beliefs both sides hold are compared.** A belief one side has and the
    other has never had is an absence, not a disagreement, and conflating them would
    report every gap in a party's knowledge as a contradiction — which for a derived
    model would be most of it, since the access gate keeps the modelled mind small on
    purpose.

    Sorted, because a dict's key order is not a contract worth asserting against.
    """
    known = {s.belief_id: s.value for s in table.core.query_scope(reference, BeliefFilter())}
    return [
        Disagreement(belief_id, model.beliefs[belief_id], known[belief_id])
        for belief_id in sorted(model.beliefs.keys() & known.keys())
        if model.beliefs[belief_id] != known[belief_id]
    ]
