---
title: How to Resolve Identity and Re-key Beliefs After a Merge
description: Record that two names are one entity, find every belief keyed on the absorbed name, and move those beliefs onto the survivor in every scope
---

# How to Resolve Identity and Re-key Beliefs After a Merge

This guide shows how to record that two entities are the same one, and then carry the beliefs that named the absorbed entity over to the survivor. The example: transcripts from two days of talks call one party "UK delegation" and "British delegation", other parties have formed beliefs about each, and the application decides they are one party.

**Before you start**

- Beliefs set up as in [How to Keep Per-Agent Beliefs as a Fold](per-agent-beliefs.md).
- The vocabulary used below:
    - identity **decisions** are events: the identity registry is a fold over them, so a decision is auditable and the registry can be rebuilt;
    - to **establish** is to declare a new entity and mint its id; to **resolve** is to find which entity a name already refers to;
    - a **merge** records that two entity ids name one referent, with one **kept** and one **absorbed**; a **split** records that one id named several;
    - to **re-key** is to move a belief whose id names the absorbed entity onto the id that names the survivor.

    [Identity](../explanation/identity.md) explains each of these.

## Who decides what

| The application decides | The library does |
|---|---|
| Whether two names are one entity (the judgment itself) | Recording the decision as an event |
| Which entity survives a merge | Following merge chains to the surviving id |
| Which scopes to re-key, and how (revise, contract, keep the old) | Listing every belief id that names the absorbed entity |
| Which product a belief belongs to after a split | Computing each belief's successor id or ids |

The library computes the re-key plan and stops there. Applying it changes beliefs, and what should happen to them is a judgment about belief that only the application can make.

```mermaid
flowchart LR
    A["merge(kept, absorbed)"] --> B["IdentityRegistry.rekeys_since(seq)"]
    B --> C["plan_rekeys(...)"]
    C --> D["Application: for each scope,<br/>revise the new id, contract the old"]
    D --> E["Belief fold catches up"]
```

## 1. Wire the identity fold beside the belief fold

The identity registry is a projection like any other: an [`IdentityFold`][identity-fold] over the log, read through an [`IdentityRegistry`][identity-registry]. Its tables, and the belief fold's entity index, share the log's SQLite file.

```python title="identity.py"
import uuid

from doxastica import BeliefFilter, InMemoryBackend, MemoryCore

from anagrapheus import register_builtin_events
from anagrapheus.beliefs import (
    BeliefEvents,
    BeliefFold,
    BeliefIndex,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    SQLiteBeliefIndexStore,
    plan_rekeys,
)
from anagrapheus.identity import (
    IdentityFold,
    IdentityRegistry,
    SQLiteIdentityStore,
    establish,
    merge,
)
from anagrapheus.timeline import (
    EventReader,
    EventTypeRegistry,
    Projector,
    SQLiteEventStore,
    UpcastRegistry,
)

types = EventTypeRegistry()
register_builtin_events(types)
predicates = PredicateRegistry(
    [Predicate("position", 2, PredicateKind.FUNCTIONAL)], namespace="fisheries"
)

store = SQLiteEventStore(":memory:")
reader = EventReader(store, UpcastRegistry(), types)
identity_store = SQLiteIdentityStore(store.connection)
index_store = SQLiteBeliefIndexStore(store.connection)
beliefs = BeliefFold(MemoryCore(InMemoryBackend()), predicates, index_store, types)
projector = Projector(store, reader, [IdentityFold(identity_store, types), beliefs])
registry = IdentityRegistry(identity_store)
events = BeliefEvents(types, predicates)
```

Put the identity fold first in the list, so identity decisions are folded before the beliefs that follow them in the same batch.

## 2. Establish entities, and form beliefs about them

[`establish`][establish] mints an entity id and returns it with the event to append. Beliefs then name entities by id:

```python title="identity.py (continued)"
# Two names arrive from two transcripts, so they are established separately.
uk, uk_event = establish(types, "UK delegation", provenance="transcript:day-1")
britain, britain_event = establish(
    types, "British delegation", provenance="transcript:day-2"
)
mackerel, mackerel_event = establish(types, "mackerel quota", provenance="agenda")
store.append([uk_event, britain_event, mackerel_event])
store.append(
    [
        events.revise("party:eu:held", "position", (uk, mackerel), "will accept 8%"),
        events.revise(
            "party:norway:held", "position", (britain, mackerel), "wants 12%"
        ),
    ]
)
projector.catch_up()
before_merge = store.last_seq()
```

`provenance` is an opaque string for your own audit trail. The library never interprets it.

!!! tip "Pin the mint when the run will be replayed"
    Minting an entity id is a random draw. Without a resolver, a replayed run mints different ids, and every belief and pin keyed on the originals is orphaned. Pass the run's resolver and stable `material`, such as the source record's own id, so a replay establishes the same entity:

    ```python
    entity_id, event = establish(
        types,
        "Faroe Islands",
        provenance="agenda",
        resolver=ledger,
        material={"source_id": "agenda-7"},
    )
    ```

    Do not leave `material` at its default when two distinct records could share a name and a provenance: they would be minted as one entity.

## 3. Record the merge

When the application has judged that "British delegation" is the UK delegation, append a [`merge`][merge] decision and bring the projections up to date:

```python title="identity.py (continued)"
# The application judges that the two names are one party.
store.append([merge(types, kept=uk, absorbed=britain)])
projector.catch_up()

print(
    registry.canonical(britain) == uk,
    registry.resolve("British delegation").sole() == uk,
)
# True True
```

After the merge:

- [`registry.canonical(id)`][canonical] follows the merge chain to the surviving id;
- [`registry.resolve(name)`][resolve] returns the candidates a name matches, with merged entities collapsed into one, and `.sole()` returns the single candidate or raises;
- the absorbed entity's names now belong to the survivor, and [`registry.history(id)`][history] keeps the record of every decision.

Matching in `resolve` is exact on the Unicode-normalised name. The library never guesses that two different names are one entity.

## 4. Plan the re-keys

The merge leaves Norway's belief keyed on the absorbed id. [`rekeys_since(seq)`][rekeys-since] lists the identity decisions after `seq` that invalidate keys, and [`plan_rekeys`][plan-rekeys] turns them into the belief ids affected and what each becomes:

```python title="identity.py (continued)"
rekeys = registry.rekeys_since(before_merge)
print(rekeys)
plans = plan_rekeys(rekeys, BeliefIndex(index_store), predicates)
for plan in plans:
    print(plan.kind, plan.belief_id, "->", plan.sole())
```

```text
[RekeyRow(seq=6, kind='merge', from_entity=UUID('…britain…'), to_entity=UUID('…uk…'))]
merge v1:fisheries:position(…britain…,…mackerel…) -> v1:fisheries:position(…uk…,…mackerel…)
```

Keep the `seq` you last re-keyed up to, and pass it to `rekeys_since` next time, so each decision is planned once.

## 5. Apply the plan in every scope

A plan says "belief id X becomes Y". It cannot say in which scopes, because the index records what was minted, not who holds it, and the belief store cannot list its scopes. Your application knows who is at the table, so this loop is yours:

```python title="identity.py (continued)"
# Applying the plan is the application's loop: which scopes, and how.
scopes = ["party:eu:held", "party:norway:held"]
for plan in plans:
    new = predicates.parse(plan.sole())
    old = predicates.parse(plan.belief_id)
    for scope in scopes:
        for state in beliefs.store.query_scope(scope, BeliefFilter()):
            if state.belief_id != plan.belief_id:
                continue
            store.append(
                [
                    events.revise(
                        scope,
                        new.name,
                        new.entity_ids,
                        state.value,
                        stance=state.stance,
                    ),
                    events.contract(scope, old.name, old.entity_ids),
                ]
            )
projector.catch_up()
```

This loop makes three decisions on the application's behalf, and yours may differ:

- A scope that never held the old belief is skipped, not given a default.
- The value and the stance carry over unchanged.
- The old belief is contracted in the same batch, so the move is atomic and both events are in the log.

This loop assumes each belief names at most one re-keyed entity. For the general case, including a belief that names two re-keyed entities and a chooser for split plans, see `apply_rekeys` in the repository's `examples/negotiation/rekeying.py`.

Re-key through events, as here, never by writing to the belief store directly: a direct write would skip the entity index and the log, and vanish on the next rebuild.

## Verify it worked

Every scope's belief now names the survivor, and the audit trail records the merge:

```python title="identity.py (continued)"
for scope in scopes:
    for state in beliefs.store.query_scope(scope, BeliefFilter()):
        print(scope, predicates.parse(state.belief_id).entity_ids[0] == uk, state.value)
print([row.kind for row in registry.history(uk)])
```

```text
party:eu:held True will accept 8%
party:norway:held True wants 12%
['established', 'merged']
```

## After a split, choose the successor

A [`split`][split] decision carves new entities out of one, and moves the names you assign to each. A belief about the original then has one candidate successor per new entity, and `plan.sole()` raises `ValueError` rather than pick one. Which product a belief belongs to depends on what the belief says, so the application must choose: inspect `plan.candidates` and the belief's value, re-key to the one you pick, and leave the belief alone, reporting it, if you cannot decide.

## Troubleshooting

### `AmbiguousSurfaceForm: 'the Commission' matched 2 entities`

[`Resolution.sole()`][resolution-sole] found more than one candidate. That is a real ambiguity in your data, and the library reports it rather than choose. Inspect `registry.resolve(name).candidates` and decide, or merge the entities if they are one. To have candidates ranked, give the registry a [`Ladder`][ladder] of your stance tokens: `IdentityRegistry(identity_store, ladder=Ladder(["suspected", "believed", "certain"]))`.

### `UnknownEntity: no entity established with id …`

`history()` was asked about an id no `establish` event introduced, or one whose `establish` event is above the identity fold's position. Run `projector.catch_up()` first.

### `MergeCycle: merge chain from … cycles at …`

Two merges point at each other, directly or through a chain, so the log contradicts itself and no entity is canonical. This version has no event that withdraws a merge, so prevent the cycle instead: before appending `merge(kept=a, absorbed=b)`, check that `registry.canonical(a) != registry.canonical(b)`.

### `UnrenderableBelief` from `plan_rekeys`

A belief id in the index cannot be re-minted by this registry: its predicate is not declared here, or it was minted by a scheme or namespace this code does not know. The error's cause says which. Pass `skip_unrenderable=True` only if you have decided those beliefs do not need re-keying.

## Related

- [How to Keep Per-Agent Beliefs as a Fold](per-agent-beliefs.md): the belief fold this guide builds on.
- [How to Pin Model and Tool Calls](pin-model-and-tool-calls.md): resolvers, which also pin entity ids.
- [Identity](../explanation/identity.md): establish versus resolve, and why decisions are events.

[identity-fold]: ../reference/anagrapheus/identity/fold.md#anagrapheus.identity.fold.IdentityFold
[identity-registry]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry
[canonical]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry.canonical
[resolve]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry.resolve
[history]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry.history
[rekeys-since]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry.rekeys_since
[establish]: ../reference/anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.establish
[merge]: ../reference/anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.merge
[split]: ../reference/anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.split
[plan-rekeys]: ../reference/anagrapheus/beliefs/rekey.md#anagrapheus.beliefs.rekey.plan_rekeys
[resolution-sole]: ../reference/anagrapheus/identity/registry.md#anagrapheus.identity.registry.Resolution.sole
[ladder]: ../reference/anagrapheus/identity/ladder.md#anagrapheus.identity.ladder.Ladder
