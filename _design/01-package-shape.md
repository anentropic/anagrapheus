# Package shape

**Created 2026-08-23.** The layout, floor, and storage decisions for anagrapheus, and
where they diverge from the upstream design corpus. Companion to
[note 00](00-testing-without-a-model.md); spec authority remains
[`agent-core` doc 00](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md).

---

## 1. anagrapheus is the stack, standalone

The upstream corpus left the stack's name open (doc 00 §10 Q1) and placed the timeline
core — codenamed **archeion** — inside the narrative-vm monorepo until a second
consumer appeared (doc 30 §6's standing policy, which wins on packaging). Both are
superseded here by a deliberate packaging decision: **anagrapheus is the stack's name
and its standalone home.** The divergence is recorded rather than argued; folding a
package back into a monorepo is mechanical, so the cost of being wrong is small and
asymmetric in our favour.

"archeion" survives as the design-doc codename for what lives at
`anagrapheus.timeline`.

## 2. Layout: descriptive subpackages

```
anagrapheus.timeline    # the event log, folds, pins, replay  (doc 00 §3–§4)
anagrapheus.identity    # later: entity shapes + registry fold (§5)
anagrapheus.beliefs     # later: belief-id minting registry over doxastica (§6)
anagrapheus.provider    # the pinned pydantic-ai adapter  (doc 00 §2, §4)
anagrapheus.chronicle   # later, behind a decision gate (§10 Q3)
```

Nothing in `timeline/` imports upward; later subpackages import it, never the
reverse.

*Amended 2026-08-29 (M4). `provider` was listed as an optional extra; it is a required
dependency like `beliefs`, and the layout comment said otherwise. Recorded rather than
silently corrected, because "optional extra" was a packaging intention and dropping it
is a decision, not a typo.*

## 3. Python floor: >=3.14

doxastica (the shipped belief store this stack consumes) requires >=3.14, so anything
depending on it cannot support less. Raising the floor also gives stdlib
`uuid.uuid7()` — no third-party uuid dependency for the envelope. CI runs one version
until 3.15 exists.

## 4. The envelope ships a `namespace` field, used

Doc 00 §8 calls the envelope freeze-content: a namespace field is cheap before the
freeze and expensive after, "decide before freezing, even if the field ships unused."
Decided — and it ships **used**, not unused: the pin ledger emits library events
(`PinPromoted`, `PinsAbandoned`, `ReplayDiverged`) from day one, under the reserved
`"anagrapheus"` namespace. The registry key is `(namespace, type, schema_version)`.

One honesty note on the resolver seam: interpretation is passed in together with
the draw, so re-reading a result never re-draws it — but `interpret` is
caller-supplied code that receives the raw value, so keeping the raw out of caller
hands is auditable discipline at the call site, not a structural impossibility. The
upstream design's stronger claim assumes a trusted resolver, which a library cannot.

Applications that would rather promote pins into their own vocabulary supply a
`promote_as` factory (and the mirroring `extract` on replay) — §8's "the library
ships the shape, the application owns the name," applied to the one place this
library emits events.

The envelope carries no `class` field. Doc 30's envelope line includes one (NVM's
event-classification vocabulary); doc 00 omits it and leaves classification ownership
open (§8 tail). We follow doc 00; the question stays open.

## 5. Storage: stdlib sqlite3, no ORM; the ports are the repository

The adapter is ~10 SQL statements over an append-only schema with explicit
transaction control. An ORM would add a heavy dependency to map mutable objects onto
a store designed never to mutate, and would interpose a session/unit-of-work between
`append` and its persist-first guarantee.

*Amended 2026-08-27 (M3). This section used to end "the charter's dependency line stays
pydantic only". That is retired: `anagrapheus.beliefs` takes a required dependency on
doxastica, which is the belief store the stack exists to integrate with. The cost is
one package — doxastica is itself pydantic-only and shares the >=3.14 floor — but the
sentence was a commitment and is no longer true, and the argument against an ORM never
needed it: an ORM is declined on what it would do to the write path, not on a count of
dependencies.*

What the repository pattern is usually asked for — SQL confined to one module,
storage swappable, callers coded against an interface — is delivered by the
`EventStore` and `PinScratchStore` Protocols, with `sqlite.py` the only module
containing SQL. The DDD Repository proper (a collection-of-aggregates illusion)
stays declined per doc 00 §3a: there is no collection of aggregates, only a stream;
read models are reached through projection query methods.

## 6. Infra-schema migrations: per-component versions

Event **payload** evolution is upcast-on-read and never touches the tables. The
tables themselves migrate via an ordered DDL list applied transactionally at connect —
a consumer's file upgrades transparently on open; a fresh file runs the whole list.
Downgrade is unsupported: a component whose recorded version exceeds the library's
latest refuses loudly.

Versions are tracked **per component** in a `schema_versions(component, version)`
table, not in `PRAGMA user_version`. Subpackages share one database file (§7) but
evolve on their own cadences: `timeline` owns `events`, `projection_positions` and
`pin_scratch`; `identity` owns the identity registry's tables. `user_version` is a
single integer per file and cannot express that, so it goes unused rather than
becoming one component's private property. Each subpackage passes its own name and
its own list to the same runner and never appears in another's.

*Amended 2026-08-25 (M2). Milestone 1 shipped `PRAGMA user_version`; the identity
layer needed a second independent list in the same file. The change was free —
nothing was released — and would not have been later.*

Guarded by a fresh==migrated schema-equivalence test and per-version fixture
databases, so old-file upgrades are exercised forever. No migration library: the
consumers are library users opening a file, not operators running a CLI, so the
runner must live inside `connect()` regardless — at which point a dependency would
just wrap what three functions do.

## 7. One database file

The pin scratch shares the events file rather than a sidecar, because promote and
abandon must move rows scratch→events atomically, which is a single transaction in
one file and an ATTACH dance across two. "Scratch is never canon" is enforced
structurally: `EventStore.read()` only ever queries `events`.

## 8. "Position", not "watermark"

The upstream design docs say **watermark** for a projection's durable record of the
last `seq` it folded. We use **position** instead (`read_position`/`commit_position`
on the port; the `projection_positions` table): "watermark" collides with the
stream-processing meaning (event-time progress markers, a different concept), while
"position" is the established event-sourcing subscription term and Kafka's consumer
term for exactly this. Same mechanism as the upstream docs; different, more
conventional name.

## 9. Folds do not declare a schedule

A `ProjectionFold` is `(name, apply)`. Milestone 1 also gave it a `clock` of
`"sync"` or `"async"`, carrying NVM's
[R6](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/16-nvm-decision-register.md)
(belief facts sync, portraits async) into the contract. Removed in M2.

The distinction R6 draws is real — some projections must be current before the next
turn resolves, others must not block it — but it is a property of **who calls
`catch_up` and when**, not of the fold. Two `Projector`s over two fold lists, driven
on two schedules, express it exactly; each fold already keeps an independent position,
so they cannot interfere. A field could only ever *describe* the intent, never act on
it: `catch_up` skipped anything non-sync and reported its stale position, so a fold
declaring `"async"` silently never ran, and nothing in the library could run it.

*Added 2026-08-26 (M2). The removal is a fix for that silent no-op, not a YAGNI trim —
the requirement is ratified upstream and is met by composition.*
