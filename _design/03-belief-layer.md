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
- **Kind.** `BeliefWriter` refuses a relational predicate whose value is not a bool.
  The arity rule catches the shape; this catches the same mistake arriving as a value.
- **Vocabulary** *(added 2026-10-12)*. A functional predicate may declare `values`, the
  words its answer can be, and the writer refuses any other. The id was schema and the
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
- **Does the writer belong here at all?** It is the only surface in this library that
  wraps another library's API rather than exposing our own, and its whole value is the
  discipline it enforces. If an application wants doxastica's API directly it can have
  it, and then the minting rule is honour-based again — which is the situation §6c
  says defeats the divergence query.
