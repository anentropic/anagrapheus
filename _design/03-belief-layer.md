# The belief layer

**Created 2026-08-27.** What `anagrapheus.beliefs` decided, and where it departs from
or fills gaps in the upstream design. Companion to
[note 02](02-identity-layer.md); spec authority remains
[`agent-core` doc 00 §6](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md).

§6 is unusual among the sections: the library it describes is **shipped**. doxastica
0.2.0 is on PyPI, and doc 00 consumes it rather than designing it. So this milestone is
not a design to implement but an *integration cost to pay* — §6a names it as such —
and most of the decisions below are about where the seam sits rather than what the
belief store does.

---

## 1. The gap that shapes the layer — [ours]

doxastica's `BeliefFilter` is closed over four fields and can filter **by**
`belief_id`. Nothing in its seam can look **inside** one. The identity layer knows
entities and has never heard of a belief.

So the question *"which beliefs mention this entity?"* has no answer anywhere in the
stack — and it is exactly the question doc 00 §5d's payoff asks. "A merge is a
retraction and the cascade returns exactly which beliefs need re-keying" cannot be
executed by either library alone; something has to hold the entity → belief-id
direction.

§6b anticipates a catalog and calls it "a legitimate but *secondary* artifact… not a
correctness mechanism". **For this seam it is one.** Not the whole catalog §6b
describes — no introspection surface, no string comparison, no suspicion-raising — but
the narrow index that makes the cascade executable. That is the sharpest thing found
building this milestone, and it is a *consequence* of doc 00 rather than a disagreement
with it: the two sections are both right and were written apart.

Ours is a fold like every other projection here: `BeliefMinted` events, a position, a
`reset()`, and rebuild-equals-incremental proved by the same property M1 and M2 use.
*(Amended 2026-10-12, [§9](#9-beliefs-as-a-projection--ours): the index is now written
by the belief fold itself, from the belief events, and `BeliefMinted` is gone.)*

## 2. Legible ids, not digests — [ours]

`v1:crm:knows(<uuid>,<uuid>)`. The alternative was a digest of canonical material,
like the pin key, which is shorter and uniform and was tempting for exactly that
reason.

Legibility is what keeps §6b's claim true. A re-key must substitute one entity for
another and re-mint; with a parseable id that is a pure function of the id, and the
index is needed only to *find* the affected beliefs. With a digest, nothing could be
recovered without the index — which would make the index the only copy of what a
belief is about, and therefore not disposable at all. Choosing the digest would have
been choosing to contradict §6b in order to save some bytes.

Three things doc 00 §6a asks for fall out of the shape:

- **`v1:`** — "version the scheme", because a changed minting rule orphans history
  exactly as pin-key drift orphans a pin. `parse` returns the scheme and a re-key
  re-mints in **the scheme of the id it replaces**; an unknown scheme raises rather
  than silently re-minting under today's rule.
- **The namespace** is a required constructor argument, not a default. Revision
  history keys on the belief id alone and is cross-scope: for a divergence query
  between two holders that collision *is* the mechanism, and for two tenants it is a
  leak. Which one you are building is not guessable, so the registry makes you say.
- **Entity ids, never prose** — the same discipline as the pin key.

## 3. Minting is derivation, not a draw — [ours]

Worth stating precisely because [note 02 §3](02-identity-layer.md) says the opposite
one layer down, and the two sit close enough to be confused.

Minting an **entity** id draws a fresh uuid and therefore consumes entropy: unpinned,
a replayed run invents a second entity. Minting a **belief** id is a pure function of
a predicate and ids already drawn: it needs no pin, replays identically by
construction, and re-minting the same proposition is a no-op rather than a hazard.

The event exists for the index, not for determinism.

## 4. Declared arity closes the trap mechanically — [ours]

§6a's functional/relational distinction is the sharpest warning in the section and it
is written as advice. Two mechanisms make it structural here:

- **Arity.** A predicate declared `knows/2` cannot be minted at arity 1, so
  `knows(alice)` with `bob` in the value — where revising to `carol` silently retracts
  the claim about bob — is not reachable by accident. This is the whole of the check
  and it needs no understanding of what a predicate means.
- **Kind.** A relational predicate refuses a value that is not a bool
  (`Predicate.check_value`, run before append since [§9](#9-beliefs-as-a-projection--ours)).
  The arity rule catches the shape; this catches the same mistake arriving as a value.
- **Vocabulary** *(added 2026-10-12)*. A functional predicate may declare `values`, the
  words its answer can be, and any other is refused. The id was schema and the
  value was not, so a live model's `hold firm` beside the domain's `hold_firm` was a
  second answer that no divergence query could compare and nothing noticed
  ([note 06 §13](06-demo-agent.md#13-open-questions)). The check is the predicate's own
  (`Predicate.check_value`) rather than the writer's, so a derived read that never
  passes through a writer can hold to it. It is optional because most answers cannot be
  listed (`location`, a name), it is refused on a relational predicate because a bool is
  already closed, and it is **not part of the id**: widening a vocabulary adds answers
  to a question that already exists, so it must not orphan the beliefs keyed on it.
  Reading stays permissive, as `parse` is: the check governs what this process writes,
  not what the log already holds.

**A `Stance` is refused as a value too**, which was not the first design. It looks
like it belongs — a relational belief's value only says whether the proposition holds,
and "how firmly" seems adjacent. It is not: doxastica has a `stance` **argument**, one
axis over, and putting a grade in the value merges ranking with epistemic state, which
is precisely what R21 separates. doxastica's own encoding refuses it independently
(values are JSON; `Stance` is not), which is a pleasant confirmation rather than the
reason.

## 5. The seam plans; it does not apply — [ours]

`plan_rekeys` consumes `IdentityRegistry.rekeys_since` and returns, per affected
belief, what it would become. A merge yields one successor — computed, not guessed. A
split yields one candidate per product and no target, because which product a belief
belongs to depends on what the belief *says*.

It stops there. Re-keying is an AGM operation with real semantics: whether the old id
is contracted or left standing, what happens when the new id already carries a belief,
whose stance survives a collision. Those are judgments about belief, and this library
owns the id and not the belief — the same line identity draws when it states the
consequence and performs no cascade.

**Chained decisions compose within a batch.** `A` absorbed into `B`, then `B` into
`C`, arriving in one `rekeys_since` batch: planning each row against the index alone
stops at `B`, because nothing was ever minted on `B` and so no row plans it onward.
The caller would re-key onto an id the same batch had already superseded. Only what
the *index* knows is planned, so the composition happens in the planner. A ring rather
than a chain raises `MergeCycleInBatch` — a ring has no last element, so there is no
successor, and identity refuses to name a canonical id for the same reason.

Splits are followed but never followed *through*: a split does not supersede its
original (the original survives it), so an entity later split has not moved, and
picking one of its products would be the judgment this layer may not make. A product
later absorbed *is* followed, per-candidate.

Two consequences worth recording:

- **Rows of one split are one plan.** Identity reports a split as several rows sharing
  a `from_entity`. Planning each alone would report it as several independent
  one-target re-keys, each of which `sole()` would answer confidently — turning "we do
  not know which" into N wrong answers.
- **A belief that cannot be re-minted is reported, not skipped.** An unknown scheme or
  an undeclared predicate means a hole in the cascade, and silence about a hole is the
  failure the seam exists to prevent. Dropping them takes an argument that says so. The
  failure is its own type, `UnrenderableBelief`, keeping the original as its `__cause__`:
  a scheme this code does not know, an id that does not parse and a predicate this
  registry does not declare are a migration problem, corrupt data and a mis-configured
  process, and a consumer that cannot tell them apart cannot respond to any of them.

## 6. What this milestone changed elsewhere

- [Note 01 §5](01-package-shape.md)'s "pydantic only" dependency line is **retired**;
  doxastica is a required dependency. It is pydantic-only itself and shares the >=3.14
  floor, so the transitive cost is one package, but the sentence was a commitment and
  is no longer true.
- [Note 02 §6](02-identity-layer.md) gave two reasons for identity not importing
  doxastica's `Stance`. The second — "importing would drag a belief store into anyone
  using the identity layer alone" — **expired** the moment doxastica became a
  dependency of the whole distribution. The decision stands on the first: grading is
  intrinsic to a `BeliefState` and not to "this referent is called X". A stale
  argument left standing is worse than no argument, so note 02 records the loss.

## 7. What review round 1 changed

Six findings, all real. Two were more than nits and are recorded above: the
chain-composition gap in §5, and the writer's silence below. The rest — an empty
namespace slipping past the "namespace is required" rule, a `PredicateKind` docstring
still offering a stance as a value after the rule changed, `UnknownScheme` standing in
for every unrenderable cause, and a 1+N query behind `beliefs_for` — were straightforward.

**The writer was a trap, and the sharpest finding of the round.** It minted ids and
said nothing, so a caller who used only `BeliefWriter` — the surface documented as
*the* way to write beliefs — got no index entries at all. `plan_rekeys` then found
nothing, and a merge silently re-keyed none of their beliefs. The layer's own front
door opened exactly the hole the layer exists to close, and the acceptance test missed
it only because the story happened to call `mint()` separately.

Writing now returns `Written(state, minted)`: the event comes back in the caller's
hand and cannot be skipped without discarding a return value. That is the same
persist-nothing discipline every other helper here follows — a belief is written
*because* something happened, and the caller owns that batch — but arrived at from the
other direction, because leaving it implicit was not merely inconvenient, it was
wrong.

It also sharpens §7's open question rather than settling it: the writer now carries
three collaborators (a belief store, a predicate registry, an event-type registry) to
enforce a discipline an application could choose to bypass entirely.

## 8. Open questions

- **Does the index need to survive a scheme bump?** Today it stores the id as minted
  and `parse` reads the scheme off it, so a v1 id and a v2 id coexist and re-key
  correctly under their own rules. What is untested is a *deployment* that bumps the
  scheme mid-life: nothing re-mints old ids, and nothing says whether it should.
- **The catalog §6b actually describes** — enumeration and introspection over
  everything minted — is still declined. The index is narrower on purpose. Build the
  rest "when enumeration gets expensive, not before".
- ~~**Does the writer belong here at all?**~~ *Closed 2026-10-12 by
  [§9](#9-beliefs-as-a-projection--ours): there is no writer; belief events are built
  and checked, then folded.* It was the only surface in this library that
  wraps another library's API rather than exposing our own, and its whole value is the
  discipline it enforces. If an application wants doxastica's API directly it can have
  it, and then the minting rule is honour-based again — which is the situation §6c
  says defeats the divergence query.

## 9. Beliefs as a projection — [ours]

**Added 2026-10-12.** The layer broke the rule it sits under. Doc 00 §2: the log is the
authority, and a layer that cannot be dropped and rebuilt "has escaped its station".
`BeliefWriter` wrote the value, the stance and the holder straight into doxastica and
returned a `BeliefMinted` to append, which recorded that an *id* had been minted and
nothing about what was believed under it. Drop the belief store and the index came
back from the log; the beliefs did not. Every edge laid with `add_edge` went with
them. §1's rebuild property was true and proved the wrong thing: it covered the index,
which was the half that never mattered on its own.

### The shape

- **Belief events, one per AGM operation.** `BeliefRevised`, `BeliefExpanded` and
  `BeliefContracted` carry the scope, the predicate, the entities, the value, the
  stance by name, and any edges. Never a belief id (doc 00 §6a): the fold mints it.
  `BeliefMinted` is removed rather than kept beside them, because minting is a pure
  function of the predicate and the entities (§3) and needs no event of its own.
- **One fold writes both stores.** `BeliefFold` reads each event an interpreter claims
  as a few operations (`Revise`, `Expand`, `Contract`), and for each one mints the id,
  upserts the index row and writes doxastica. The index and the beliefs can no longer
  disagree about what was minted, because they are written from the same operation.
- **Applications bring their own events.** An interpreter is a pure function from an
  event to operations, keyed by `(namespace, type)`. The library's three events have
  built-in interpreters; an application whose own event already *is* the belief (an
  observation, a remark overheard) binds it to one and never emits a library event
  beside it. Doc 00 §8's rule again: the library ships the shape, the application owns
  the name. This is the shape NVM's belief ingestion needs, beliefs derived from
  observation events with the log the only place they live.
- **`BeliefEvents` builds the library's events and writes nothing.** It returns a
  `PendingEvent`, so the caller owns the batch and its transaction, as with every
  other helper here.

### Source and cause are different questions

A state's `source_event_id` is **always the event it was folded from**. The first
draft wanted it to be the event that *occasioned* the belief (an observation, a
merge), which is what the old acceptance test supplied. Two things decide against it.
The fold needs the source to recognise its own writes (below), and doxastica's as-of
order should be the log's order, which it is only if the source is the event at that
position. "Why does this scope believe that" is still answerable, and answered the way
the rest of the stack answers it: the belief event's envelope carries the reason as
`causation_id`. For an application event folded through its own interpreter the two
coincide, which is the case that motivated wanting them to.

### Idempotence over a store outside the transaction

`Projector` commits a fold's position inside the log's transaction. doxastica is not
in it, so a crash between the two re-applies the last batch, and `revise` is not
idempotent. The fold therefore skips any operation whose scope already holds a state
for this belief sourced from this event, found through `get_revision_chain`. That is
exact rather than heuristic because of the source rule, and because one event may
write one proposition in one scope **at most once**. An interpreter that writes it
twice is refused: the second write could not be told apart from a replay of the
first.

### Edges name states by what the log knows

doxastica mints `state_id` as a uuid7, so it differs on every fold and cannot go in an
event. An edge in an event names its target as a `StateRef(scope_id, belief_id,
event_id)`, which resolves to the same state on every fold. A ref that resolves to
nothing raises `UnresolvedStateRef`. doxastica's `add_edge` would quietly lay nothing,
and here that would be a dependency the log records and the store lacks, so a later
retraction would stop reporting what it compromised.

### Checks run before append

Once an event is canon, an operation that fails a check fails on every replay, and a
fold that raises is a projection stuck on it. So `check(predicates, ops)` (declared
predicate, arity, value kind, vocabulary, no double write) runs **before** the event
exists. `BeliefEvents` calls it on every build. An application calls it on what its
interpreter makes of its own event before appending that event. The fold runs the
same check again and raises, so a bad event that got past is loud rather than a
belief silently missing.

**Canonicalise at write time.** An interpreter must be pure, so anything that needs a
lookup (resolving a name to an entity, choosing among candidates) happens before the
event is written and its answer goes in the payload. The fold never asks the identity
layer anything.

### Reset

doxastica has no "clear". `BeliefFold.reset(store)` takes the empty store to fold
into and empties the index beside it, and `BeliefFold.store` is where readers find the
current one. The caller owns that swap because only the caller knows what kind of
store it is. One consequence is easy to miss: **an in-memory belief store with a
persisted position is stale on reopen**. The position says the beliefs were folded;
the process that held them is gone. The demo's `Table.open` rebuilds the fold when the
position is non-zero, and the M3 acceptance test now ends by losing its beliefs to a
fresh process and getting them back.

### What this changed elsewhere

- `BeliefWriter`, `BeliefIndexFold`, `BeliefMinted` and `mint()` are removed. The
  index store's `minted` takes a `BeliefRow`.
- §7's trap, a minting event the caller could forget to append, has no successor:
  there is no second half to forget.
- [Note 06](06-demo-agent.md): `Table.believe` appends a `BeliefRevised` and returns
  the folded state; overheard positions are written as one batch citing the
  `PositionStated`; the re-key loop appends a revise and a contract; readings rest on
  their premise through an edge in the event instead of `add_edge`.
- The demo's prompts do not change, because the chronicle renders only domain events,
  so the committed recordings still replay.

The proof is three tests that drop the store and replay the log: a property over
random revise, expand and contract sequences (straight, interrupted at any point and
in any batch size, and rebuilt, all equal apart from `state_id`), the M3 story, and a
whole recorded negotiation, edges and impact included.
