"""
The negotiating table: everything wired together, and the queries it can answer.

This is the composition root a real consumer would write — one store, one registry,
one predicate registry, and a writer per scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from doxastica import InMemoryBackend, MemoryCore

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import BeliefWriter, Disagreement, divergence
from anagrapheus.beliefs import SQLiteBeliefIndexStore
from anagrapheus.identity import IdentityFold, SQLiteIdentityStore, establish
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

from examples.negotiation import domain
from examples.negotiation.scopes import held, model_of, stated

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from anagrapheus.beliefs import PredicateRegistry
    from anagrapheus.timeline import PendingEvent

__all__ = ["Table"]


@dataclass
class Table:
    """A negotiation in progress: the log, the belief store, and who is at it."""

    store: SQLiteEventStore
    reader: EventReader
    types: EventTypeRegistry
    core: MemoryCore
    predicates: PredicateRegistry
    identity: SQLiteIdentityStore
    projector: Projector
    index_store: SQLiteBeliefIndexStore | None = None
    #: Party name -> entity id. Parties are **identity-layer entities**, not strings:
    #: a belief names entities by id, so a party that can be merged must have one.
    #: Discovered by trying it the other way -- see note 06.
    seats: dict[str, UUID] = field(default_factory=dict)
    clauses: dict[str, UUID] = field(default_factory=dict)

    @classmethod
    def open(cls, path: Path, *parties: str) -> Table:
        """Stand up a table over ``path``, establishing each party as an entity."""
        types = EventTypeRegistry()
        register_builtin_events(types)
        domain.register(types)
        store = SQLiteEventStore(path)
        identity = SQLiteIdentityStore(store.connection)
        reader = EventReader(store, UpcastRegistry(), types)
        table = cls(
            store=store,
            reader=reader,
            types=types,
            core=MemoryCore(InMemoryBackend()),
            predicates=domain.predicates(),
            identity=identity,
            projector=Projector(store, reader, [IdentityFold(identity, types)]),
        )
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
        self.append(self.types.pending(domain.ClauseTabled(clause_id=clause_id, title=title)))
        self.clauses[title] = clause_id
        return clause_id

    @property
    def parties(self) -> tuple[str, ...]:
        """The seated parties, by name."""
        return tuple(self.seats)

    # ---- writing ---------------------------------------------------------------------

    def writer(self, scope: str) -> BeliefWriter:
        """A writer into one scope. Every scope mints through the same registry."""
        return BeliefWriter(self.core, self.predicates, self.types, scope_id=scope)

    def append(self, *events: PendingEvent) -> int:
        return self.store.append(events)

    def believe(
        self,
        scope: str,
        predicate: str,
        entity_ids: tuple[UUID, ...],
        value: object,
        source_event: UUID,
    ) -> None:
        """
        Hold a belief in ``scope``, **and append the minting event**.

        The second half is the part worth a helper. ``BeliefWriter.revise`` returns
        ``Written(state, minted)`` and leaves appending ``minted`` to the caller — by
        design, since the writer does not own the log. Drop it and the belief is held
        but invisible to the index, so ``plan_rekeys`` will not see it when an identity
        decision later moves the entity it names, and the re-key silently misses.

        That failure is quiet: everything reads correctly until a merge, which then
        appears to work while leaving beliefs keyed on an entity that no longer names
        anything. Every consumer needs this pairing, so it is written once here.
        """
        written = self.writer(scope).revise(predicate, entity_ids, value, source_event)
        self.append(written.minted)

    # ---- the three questions ---------------------------------------------------------

    def misreading(self, holder: str, other: str) -> list[Disagreement]:
        """
        How wrong ``holder`` is about ``other`` — the game-theoretic quantity.

        The agent acts on this: a concession is sized by what it believes the other side
        actually wants, so an error here is an error in the offer.
        """
        return divergence(self.core, model_of(holder, other), held(other))

    def own_gap(self, party: str) -> list[Disagreement]:
        """
        The distance between what ``party`` says and what it wants.

        **Nobody but that party can ask this**, because nobody else has its ``held``
        scope. That asymmetry is what makes the domain strategic rather than merely
        multi-party, and it is not expressible at all in a design with one believer.
        """
        return divergence(self.core, stated(party), held(party))

    def taken_at_their_word(self, holder: str, other: str) -> list[Disagreement]:
        """
        Where ``holder``'s model of ``other`` departs from what ``other`` has said.

        Distinct from :meth:`misreading`: this measures scepticism, not error. A holder
        can be right to disbelieve a stated position, and this query does not know which.
        """
        return divergence(self.core, model_of(holder, other), stated(other))

    # ---- scope enumeration -----------------------------------------------------------

    def scopes(self) -> list[str]:
        """
        Every scope at this table.

        Derived from the parties, **not** from the belief store, which cannot enumerate
        scopes — a limitation that matters as soon as a re-key has to visit all of them.
        See :mod:`examples.negotiation.rekeying`.
        """
        found = [f(p) for p in self.parties for f in (held, stated)]
        found += [model_of(a, b) for a in self.parties for b in self.parties if a != b]
        return found

    def close(self) -> None:
        self.store.close()
