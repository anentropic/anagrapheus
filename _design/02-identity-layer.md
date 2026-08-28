# The identity layer

**Created 2026-08-25.** What `anagrapheus.identity` decided, and where it departs from
or fills gaps in the upstream design. Companion to
[note 01](01-package-shape.md); spec authority remains
[`agent-core` doc 00 §5](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md).

Doc 00 flags §5 as the one section with no NVM ruling behind it — **[new]**, and "the
least tested part of the design". Several decisions below are therefore ours to make
rather than ours to implement, and each is marked as such.

---

## 1. The boundary is the whole design

The layer table gives identity *canonical referents and the record of identity
decisions*, and forbids it *what two referents mean*. Everything here holds that line:

- `resolve()` matches **exactly**, on the NFC-normalized surface form. NFC is a claim
  about representation — two encodings of one character are one character. Case
  folding, whitespace collapsing and accent stripping are claims about meaning, and an
  application that wants them normalizes before it gets here.
- `resolve()` returns **candidates, not an answer** — a `Resolution`, never
  `UUID | None`. §5c is explicit that identity buys governance, not inference:
  something still has to judge that `A. Smith` is `alice@corp`. A method handing back a
  bare id would dress a lookup up as a decision. `Resolution.sole()` exists for callers
  who have already established that a form is unambiguous, and raises rather than
  guessing so the assumption stays visible.
- `provenance` is an opaque `str`. NVM's R9 two-axis vocabulary (origin /
  epistemic_source) is *its* domain semantics; adopting it would be this library
  owning meaning.

## 2. Vocabulary: library default plus per-role override

Doc 00 §8 names the identity layer as the first thing to strain "the library ships the
shape, the application owns the name" — NVM already emits `EntityEstablished`, so a
library claiming that name collides on day one.

The `namespace` field ([note 01 §4](01-package-shape.md)) removes the *collision*
argument §8 rested on, but not the ownership one. So: an `IdentityVocabulary` binds
each of the four **roles** to an event type plus an adapter, and the default binds this
library's own models under the reserved namespace. An application with no opinion gets
a working layer; NVM binds `EntityEstablished` and never sees a library type. Same
relationship `promote_as` has with the pin ledger, one layer up.

The binding names `(namespace, type)` and deliberately not a schema version: payloads
reach the fold already upcast, so a version bump needs no change to a vocabulary.

**Reading is polymorphic; writing is yours.** The `establish`/`alias`/`merge`/`split`
helpers serve the default vocabulary only. An application that owns its names already
owns its payload construction — but it should still use `mint_entity_id`, because §3's
hazard is not a property of the names.

## 3. Minting an entity id consumes entropy — [ours]

Not in doc 00, and the sharpest thing found while building this.

Establishing mints a fresh id. That is a nondeterministic effect, so it is exactly what
[§4a](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md#4a-key-on-what-consumes-the-entropy)
says to key on. Under generation-replay an unpinned mint draws a *different* id, and
every belief, pin and reference keyed on the original silently orphans — the same
failure mode as pin-key drift, reached from the other direction.

`mint_entity_id(material, resolver=...)` draws the id through the pin ledger, so a
replayed run re-establishes the same entity. Without a resolver it is a plain `uuid7`
and the docstring says what that costs. Both halves are tested: the pinned mint
replays identically, and the unpinned one is asserted *not* to.

## 4. `Split` — filling a hole in the corpus — [ours]

Doc 00 §5c writes the event as `Split(...)` and stops; there is no field list to
implement. Ours:

```python
Split(original, into=[SplitAssignment(entity_id, surface_forms)], source_event)
```

Each assignment mints a new entity and names the forms that move to it; forms left
unnamed stay with `original`, which **survives** the split. A split is not a merge —
nothing yields to anything, so `canonical()` is unaffected for every id involved.

Worth taking upstream — but as a **design-doc edit to doc 00 §5c**, not a ruling.
`_design/agent-core/README.md` is explicit that the directory mints no `R`-numbers,
and the v2 register rules on NVM-owned mechanisms, which identity is not: NVM has the
establish half and never needed the rest. Until that edit lands this is anagrapheus's
shape, not narrative-vm's.

## 5. The re-key seam names every target — [ours]

§5d gives the payoff: a merge means every belief keyed on the absorbed entity is now
suspect, and the belief layer's retraction cascade turns that into a list of states to
re-key. `IdentityRegistry.rekeys_since(seq)` is where that consequence leaves this
layer.

A merge emits **one** row naming its target. A split emits **one row per new entity**.
The first design here used a null target for splits to mean "ambiguous"; naming every
product instead is strictly more information at no cost in honesty — which of a split's
products a given belief belongs to depends on what the belief *says*, which this layer
may not interpret, and several rows sharing a `from_entity` states exactly that.

Identity states the consequence and performs no cascade. It does not know what a
belief is.

**A merge gathers the absorbed entity's surface forms onto the survivor** — added in
review round 2. Leaving them under the absorbed id made the projection inconsistent
with itself: `resolve()` canonicalized those forms onto the survivor while
`surface_forms()` did not list them, and a later `Split` of the survivor could not
reach them, so the form it was asked to disambiguate still matched two entities. The
log is untouched and `history()` keeps the record of which entity recorded each form;
only the projection consolidates. Where both entities held the same form, the
survivor's own row wins — deterministic without ranking, which this layer cannot do.

## 6. Stance is a slot, not a scale — [ours]

`Aliased` and `Established` carry a **`stance` token that this library never
interprets**, exactly like `provenance` and for exactly the same reason. Milestone 2
first shipped a typed `Stance` enum mirroring
[R21](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/16-nvm-decision-register.md)'s
ladder; that was wrong, and the argument that retired it is worth keeping.

**Why not a library-owned ladder.** Two structurally identical fields sat one line
apart under opposite rules: `provenance` opaque because R9's axes are NVM's domain
semantics, `stance` typed despite R21's ladder being domain semantics by the same
argument. The only reason for the difference was that doc 00 §5c writes the signature
as `Aliased(entity_id, surface_form, stance)`, and the signature got implemented
without being interrogated.

**Why not import doxastica's.** doxastica exports a `Stance` and there is no import
cycle — it depends on pydantic alone and identity does not feed it. The reason is
about what the type *is for*. doxastica's `BeliefState` has seven closed fields and
`stance` is a required one: AGM revision cannot work without a grade, because the
grade is what drives displacement. Grading is **intrinsic** to the abstraction
doxastica implements. It is not intrinsic to "this referent is called X" — that
proposition is complete without one. Importing would drag a belief store into
anyone using the identity layer alone, to record an annotation that is optional.

*Amended 2026-08-27 (M3): the second of those two reasons has expired.*
[Note 03](03-belief-layer.md) makes doxastica a required dependency of the whole
distribution, so importing `Stance` here would no longer drag anything anywhere — a
consumer of the identity layer alone now carries the belief store regardless. The
decision stands on the first reason, which is the load-bearing one and is unaffected:
grading is intrinsic to a belief and not to a name. Recorded rather than quietly kept,
because an argument that no longer holds is worse than no argument — a later reader
would weigh two reasons where there is one.

**Why not an integer rank.** Tempting, because a number sorts in SQL. Rejected on
NVM's own grounds and one of our own:

- doc 18 classifies confidence as its category **F** — *"a quantity used as if it had
  semantics that nothing actually defines; the risk is arithmetic over numbers that
  mean nothing."* An int with no library vocabulary reproduces that at the library
  boundary: two consumers can disagree about the direction and granularity of the scale.
- R21 separates *ranking advice* from *epistemic state* explicitly — "a pathway's
  search score never becomes stance". Making the grade numeric **so that it can be
  ranked in a query** is that merge.
- R25 names the general pattern while retiring `BalanceConstraint.tolerance`: a
  semantic judgment wearing a float.
- **Ours:** ranks are position-dependent. Insert a rung between two existing ones and
  every stored integer silently re-grades, with no upcast able to detect it. Tokens
  do not have that failure mode — which is very likely why doxastica ranks by
  `.value` internally but *serializes `.name`*.

**What we ship instead.** A `Ladder` — the application's own tokens, weakest first,
declared once and handed to `IdentityRegistry`. The layer orders by it in SQL (a
`CASE` over bound rung parameters) without knowing what any rung means. With no
ladder there is no ranking and candidates come back in `seq` order, which is the
honest default: a layer that returns "candidates, not an answer" should not hand them
back pre-ranked by a scale it invented. An unrecognised token — including
`UNGRADED` — ranks below every rung rather than raising, because the log is the
authority and may hold a vocabulary this process has never seen.

An NVM-style consumer writes `Ladder(tuple(s.name for s in doxastica.Stance))` and
gets R21's ladder back, with R21's assignment rule staying where it belongs: in the
application.

**Consequence for `Established`.** It no longer records its first surface form at
`certain`; the default is `UNGRADED`, with an optional `stance` for a caller who
knows better. This closes the open question the first draft of this note recorded:
for an *established* entity a top grading would be true, for a *resolved* one the
first form is a claim like any other, and this layer cannot tell the two apart.

## 7. Storage

The projection's four tables live in the timeline's file, sharing its connection and
therefore its transactions — which is what lets projection state and fold position
commit together, so a crash cannot leave the registry ahead of the position guarding
it. `SQLiteEventStore.connection` exists for exactly this.

Schema versioning moved to per-component rows to make this possible; the reasoning is
in [note 01 §6](01-package-shape.md#6-infra-schema-migrations-per-component-versions).

A merge cycle (`A` into `B`, `B` into `A`) is detected at **read**, not at write:
`canonical()` raises `MergeCycle`. The fold records what the log says and does not
adjudicate it, which is the same discipline as everywhere else here — a bad merge is
corrected by appending a superseding event, per §5c.

## 8. Open questions

- **Does NVM adopt this subset?** Doc 00 §10 Q5. NVM would use *establish, never
  merge*. The merge machinery is inert under that usage, but §10 Q5's warning stands:
  a first consumer exercising only half an interface is weak evidence for the half it
  skips.
- **Should doc 00 §5c take this `Split` shape?** §4 above fills a blank the corpus
  left. Worth carrying upstream alongside two other things doc 00 lacks: §3's
  pinned-minting hazard, and §5d's seam emitting one row per split product.
- **Nothing enforces the establish/resolve split.** §5b says engine-created entities
  should be established and only externally-referring ones resolved. That is a
  discipline the library records but cannot check — `provenance` is where an
  application would encode it.
