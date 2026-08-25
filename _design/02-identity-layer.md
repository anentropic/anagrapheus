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

Worth proposing upstream as a ruling. Until then this is anagrapheus's shape, not
narrative-vm's.

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

## 6. Stance: aligned with R21, not imported

`Aliased` carries a stance, and NVM's
[R21](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/16-nvm-decision-register.md)
(RATIFIED) retired numeric confidence in favour of an ordinal enum,
`certain > believed > suspected > doubted`, with **comparison as the only permitted
operation**.

We use that ladder, defined locally rather than imported from doxastica: identity must
not depend on the belief layer that sits above it. It is a plain `Enum` with
`total_ordering` — deliberately not `IntEnum`, which would permit the arithmetic R21
forbids. A test asserts that `Stance.CERTAIN + Stance.DOUBTED` raises.

**Open for P3:** reconcile against whatever doxastica actually exports. If the two
ladders agree, the duplication is documentation; if they diverge, that is a finding.

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
- **Should `Split` be an upstream ruling?** §4 above invents a shape the corpus left
  open. It should either be ratified there or consciously left as ours.
- **Is `first_surface_form` at `CERTAIN` right?** Establishing records the form at
  `CERTAIN` because the system chose the name itself. For an *established* entity that
  is trivially true; for a resolved one, the first form is a claim like any other and
  arguably should carry a caller-supplied stance.
- **Nothing enforces the establish/resolve split.** §5b says engine-created entities
  should be established and only externally-referring ones resolved. That is a
  discipline the library records but cannot check — `provenance` is where an
  application would encode it.
