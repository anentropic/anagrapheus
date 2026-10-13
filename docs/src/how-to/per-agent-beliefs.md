---
title: How to Keep Per-Agent Beliefs as a Fold
description: Give each agent its own revisable beliefs, derived from the event log so they can be dropped and rebuilt, and measure where two agents disagree
---

# How to Keep Per-Agent Beliefs as a Fold

This guide shows how to give each agent in a simulation its own beliefs, written as events and folded into a belief store, so that two agents can hold different answers to the same question and you can query where they differ. The example is a fisheries negotiation in which the UK privately wants a 5% cut and publicly asks for 12%.

**Before you start**

- `anagrapheus` installed. Beliefs are stored by [doxastica](https://pypi.org/project/doxastica/), a separate library installed with it. anagrapheus supplies the belief ids and the fold; doxastica keeps the beliefs.
- The vocabulary used below:
    - a **scope** is whose beliefs these are: one per holder, named by you (`party:uk:held`);
    - a **predicate** is a declared question with a fixed number of entity arguments (`position(party, clause)`), and a **proposition** is a predicate applied to particular entities;
    - **revise** replaces what a scope held for a proposition, **expand** adds a belief without replacing, and **contract** retracts one;
    - a **fold** applies events in order to build state, and a **projection** is state built that way, which can be dropped and rebuilt from the log.

    [Beliefs](../explanation/beliefs.md) explains each of these.

## Who decides what

| The application decides | The library does |
|---|---|
| The predicate vocabulary: names, arity, kinds and value words | Minting belief ids from predicates, and nowhere else |
| How scopes are named, and which exist | Folding belief events into each scope |
| Which of its events carry beliefs, through interpreters | Checking arity and values before the event is appended |
| What a disagreement between two values means | Listing where two scopes hold different values |

## 1. Declare the predicates

Predicates are schema: declare them once, in code, in a [`PredicateRegistry`][predicate-registry]. The `namespace` is required, and goes into every belief id this registry mints.

```python title="beliefs.py"
import uuid
from collections.abc import Sequence
from uuid import UUID

from doxastica import BeliefFilter, InMemoryBackend, MemoryCore, Stance

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    BeliefEvents,
    BeliefFold,
    BeliefIndex,
    BeliefOp,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    Revise,
    SQLiteBeliefIndexStore,
    divergence,
)
from anagrapheus.timeline import (
    DomainEvent,
    Envelope,
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

# 1. Declare the predicates: the questions a belief can answer.
predicates = PredicateRegistry(
    [
        Predicate("position", 2, PredicateKind.FUNCTIONAL),  # position(party, clause)
        Predicate(
            "red_line",
            2,
            PredicateKind.FUNCTIONAL,
            values=frozenset({"concede", "hold_firm", "never"}),
        ),
        Predicate("witnessed", 2, PredicateKind.RELATIONAL),  # witnessed(party, event)
    ],
    namespace="fisheries",
)
```

Two modelling choices are yours here:

- **Functional or relational.** A `FUNCTIONAL` predicate holds one value at a time: revising `position(uk, mackerel)` replaces the old position. A `RELATIONAL` predicate puts every argument in the id and takes a `bool` value saying whether the proposition holds. `witnessed(uk, remark)` is relational, so believing the UK witnessed a second remark does not retract the first.
- **A closed vocabulary.** `values=` limits a functional predicate's answers to a set of words, so `"hold firm"` and `"hold_firm"` cannot become two different answers.

## 2. Bind your events to an interpreter

When one of your domain events already carries a belief, as a public statement carries what a party has said, write an **interpreter**: a function from the event to the belief operations it implies. It must be a pure function of the event, because it runs again on every rebuild.

```python title="beliefs.py (continued)"
# 2. An application event that carries belief, and its interpreter.
class PositionStated(DomainEvent, frozen=True):
    party: str
    party_id: UUID
    clause_id: UUID
    position: str


def stated_position(envelope: Envelope, event: DomainEvent) -> Sequence[BeliefOp]:
    assert isinstance(event, PositionStated)
    return (
        Revise(
            f"party:{event.party}:stated",
            "position",
            (event.party_id, event.clause_id),
            event.position,
        ),
    )


types = EventTypeRegistry()
register_builtin_events(types)
types.event("fisheries", "PositionStated")(PositionStated)
```

Operations name a belief by predicate and entity ids ([`Revise`][revise], [`Expand`][expand], [`Contract`][contract]), never by a belief id, so an interpreter cannot mint around the registry. Entities are always UUIDs; put them in the event's payload when it is written.

For beliefs that no domain event carries, such as a seeded private goal or a conclusion a model wrote, use the library's own belief events instead (step 4).

## 3. Wire the fold

A [`BeliefFold`][belief-fold] folds belief-bearing events into a doxastica store, and keeps an index from each entity to the belief ids that name it. A [`Projector`][projector] runs it over the log.

```python title="beliefs.py (continued)"
# 3. Wire the fold to a store and a projector.
store = SQLiteEventStore(":memory:")
reader = EventReader(store, UpcastRegistry(), types)
fold = BeliefFold(
    MemoryCore(InMemoryBackend()),
    predicates,
    SQLiteBeliefIndexStore(store.connection),
    types,
    interpret={("fisheries", "PositionStated"): stated_position},
)
projector = Projector(store, reader, [fold])
```

The `interpret` mapping is keyed by `(namespace, type)`. The fold understands the library's own belief events without being told, and skips every event that no interpreter claims.

## 4. Write beliefs as events

Never write to the belief store directly: a belief written there is not in the log and vanishes on the next rebuild. Build library belief events with [`BeliefEvents`][belief-events], which checks each one against the predicates before you append it:

```python title="beliefs.py (continued)"
uk, mackerel = uuid.uuid7(), uuid.uuid7()

# 4. Write beliefs as events: a private goal, then a public statement.
beliefs = BeliefEvents(types, predicates)
store.append(
    [
        beliefs.revise("party:uk:held", "position", (uk, mackerel), "cut 5%"),
        beliefs.revise(
            "party:uk:held",
            "red_line",
            (uk, mackerel),
            "never",
            stance=Stance.believed,
        ),
        types.pending(
            PositionStated(
                party="uk", party_id=uk, clause_id=mackerel, position="cut 12%"
            )
        ),
    ]
)
print(projector.catch_up())
# {'anagrapheus.beliefs': 3}
```

`revise`, `expand` and `contract` each return an event ready to append, so you append it in the same batch as whatever caused it. Pass `causation_id=` to record that cause in the envelope. `stance=` says how firmly the belief is held (`doubted`, `suspected`, `believed`, `certain`), separately from its value.

The UUIDs here are made up on the spot. In a real application, parties and clauses are entities established through the identity layer, so a later merge can re-key the beliefs that name them. See [How to Resolve Identity and Re-key Beliefs After a Merge](identity-and-rekey.md).

## 5. Read per scope and measure disagreement

Read one scope's current beliefs from the fold's store, and compare two scopes with [`divergence`][divergence]:

```python title="beliefs.py (continued)"
# 5. Read per scope, and measure the gap between what the UK holds and says.
for state in fold.store.query_scope("party:uk:held", BeliefFilter()):
    print(state.belief_id, "=", state.value, state.stance.name)
print(divergence(fold.store, "party:uk:held", "party:uk:stated"))
print(BeliefIndex(SQLiteBeliefIndexStore(store.connection)).beliefs_for(mackerel))
```

The output, with UUIDs shortened:

```text
v1:fisheries:position(01a1…,01a1…) = cut 5% certain
v1:fisheries:red_line(01a1…,01a1…) = never believed
[Disagreement(belief_id='v1:fisheries:position(01a1…,01a1…)', held='cut 5%', reference='cut 12%')]
[BeliefRow(belief_id='v1:fisheries:position(…)', predicate='position', …), BeliefRow(belief_id='v1:fisheries:red_line(…)', predicate='red_line', …)]
```

`divergence(store, holder, reference)` compares only beliefs both scopes hold. A belief one scope has and the other lacks is an absence, not a disagreement, so it is not reported. Whether a reported difference matters is for your application to judge.

Always read through `fold.store`, not a reference to the store you passed in: a rebuild replaces it.

## Verify it worked

Drop the beliefs and fold them again from the log. The same beliefs come back:

```python
from anagrapheus.beliefs import FOLD_NAME

fresh = MemoryCore(InMemoryBackend())
projector.rebuild(
    FOLD_NAME, reset=lambda: fold.reset(fresh), reset_in_transaction=True
)
print(divergence(fold.store, "party:uk:held", "party:uk:stated"))
# [Disagreement(belief_id='v1:fisheries:position(…)', held='cut 5%', reference='cut 12%')]
```

`reset_in_transaction=True` is correct here because the belief index shares the log's database, so emptying it and zeroing the fold's position commit together.

!!! warning "An in-memory belief store needs a rebuild when the file is reopened"
    The fold's position is stored in the SQLite file, but a `MemoryCore(InMemoryBackend())` lives in memory. Reopen the file in a new process and the position says the beliefs are folded while the store is empty. When you reopen a database, check `store.read_position(FOLD_NAME)` and, if it is above zero, rebuild as shown above before reading beliefs.

## Troubleshooting

### `WrongArity: predicate 'position' takes 2 entities, got 1`

The belief was built with the wrong number of entities. Arity is fixed when the predicate is declared. A relational belief keyed on too few entities would silently retract claims about the missing one, so the registry refuses it.

### `UnknownPredicate: predicate 'quota' is not declared`

Predicates exist only if declared. Add it to the `PredicateRegistry` and deploy. Every process that mints or reads beliefs must use the same declarations.

### `OutsideVocabulary: predicate 'red_line' answers with one of ['concede', 'hold_firm', 'never'], not 'hold firm'`

The value is not one of the declared words. Normalise it before building the event. If a model produced it, constrain the model's output schema to the same words.

### `TypeError: predicate 'witnessed' is relational, … pass a bool, not str`

A relational predicate's value only says whether the proposition holds. Put the extra information into the entity arguments, or declare a functional predicate instead. How firmly it is held goes in `stance=`, not in the value.

### The projector raises on one of your own events, every time it runs

By the time the fold sees an event, the event is permanent. An event whose interpreter produces an invalid operation fails the fold on every replay, which leaves the projection stuck at that event. Check your own events before appending them by running the interpreter's output through `beliefs.check(*ops)`:

```python
event = PositionStated(party="uk", party_id=uk, clause_id=mackerel, position="cut 12%")
beliefs.check(*stated_position(None, event))  # raises if the fold would refuse it
```

## Related

- [How to Resolve Identity and Re-key Beliefs After a Merge](identity-and-rekey.md): when two names turn out to be one party.
- [How to Assemble Prompts from Granted Slices](assemble-prompts.md): showing an agent only its own scopes.
- [Beliefs](../explanation/beliefs.md): scopes, predicates and the three operations explained.
- [The Event Log and Projections](../explanation/event-log-and-projections.md): folds, positions and rebuilds.

[predicate-registry]: ../reference/anagrapheus/beliefs/predicates.md#anagrapheus.beliefs.predicates.PredicateRegistry
[revise]: ../reference/anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Revise
[expand]: ../reference/anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Expand
[contract]: ../reference/anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Contract
[belief-fold]: ../reference/anagrapheus/beliefs/fold.md#anagrapheus.beliefs.fold.BeliefFold
[belief-events]: ../reference/anagrapheus/beliefs/writer.md#anagrapheus.beliefs.writer.BeliefEvents
[divergence]: ../reference/anagrapheus/beliefs/divergence.md#anagrapheus.beliefs.divergence.divergence
[projector]: ../reference/anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector

<!-- INTERNAL NOTES FOR EDITOR - DO NOT INCLUDE IN RENDERED OUTPUT
## Page Classification
- Type: how-to
- Reference used: skills/diataxis/references/how-to-guides.md

## Source References
- src/anagrapheus/beliefs/predicates.py, ops.py, events.py, writer.py, fold.py, divergence.py, index.py, sqlite.py
- tests/beliefs/conftest.py, tests/beliefs/test_fold.py
- examples/negotiation/domain.py (predicates), scopes.py (scope naming), table.py (rebuild on reopen)
- Verified scripts: scratchpad docs-examples/howto/beliefs.py, beliefs_errors.py

## Accuracy Notes
- `BeliefEvents` exists and works as the inventory described (revise/expand/contract return PendingEvent; check() validates ops). Verified.
- Error messages in Troubleshooting are verbatim from beliefs_errors.py, with the TypeError one abridged by an ellipsis.
- `check(*stated_position(None, event))` passes None as the envelope; that works only because this interpreter ignores its envelope argument. Verified.
- Rebuild output verified (divergence unchanged after rebuild).
- The in-memory-store-on-reopen hazard is real: examples/negotiation/table.py rebuilds beliefs on open for exactly this reason.
- Index rows: `minted` is an upsert, so `position` (minted at seq 1 and again at seq 3) appears once with seq=1. The output block is abridged.

## Known Limitations
- doxastica has no scope enumeration (no `scopes()`); the application must know its scope names. Mentioned implicitly in "Who decides what".
- The doxastica PyPI link is assumed; Editor may want to verify or drop it.

## API Reference Links Used
- anagrapheus.beliefs.predicates.PredicateRegistry
- anagrapheus.beliefs.ops.Revise, Expand, Contract
- anagrapheus.beliefs.fold.BeliefFold
- anagrapheus.beliefs.writer.BeliefEvents
- anagrapheus.beliefs.divergence.divergence
- anagrapheus.timeline.folds.Projector
-->
