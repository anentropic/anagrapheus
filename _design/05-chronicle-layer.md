# The chronicle layer

**Created 2026-08-30.** What `anagrapheus.chronicle` decided, and where it departs from
or fills gaps in the upstream design. Spec authority is
[`agent-core` doc 00 §7](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md);
the grade question is settled by
[**R61**](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/16-nvm-decision-register.md),
which is **PROPOSED 2026-08-30 and awaiting sign-off** — nothing here states it as
current, and if it moves in sign-off this follows.

Doc 00 §7 keeps four things and discards the rest as commodity: consolidate ranges of
the log into prose that **cites the range it covers**; supersede rather than correct;
split recency from depth; return a distilled result rather than raw chunks. This layer
is those four, and the retrieval stack §7 declines to extract stays declined.

This milestone also answers doc 00 §10 Q3 — *"whether chronicle anchoring ships as an
archeion projection helper or stays purely a documented pattern"* — in favour of the
helper. §10 itself is untouched: R61's consequences list defers the doc sweep to
ratification.

---

## 1. Fold determinism is a property of the fold — [R61 clause 1]

The mechanism lives in `timeline`, not here. R61 rules **the class, not the instance**:
a prose summariser is the obvious generative fold, but a portrait regenerator or any
other model-written read model is the same shape. So `ProjectionFold` gained
`fold: "deterministic" | "generative"`, and the chronicle is its first consumer.

**Required, never defaulted.** A default would be `"deterministic"`, and a generative
fold that forgot to declare would inherit a guarantee it cannot keep — silently, since
nothing about a model call looks different from outside the fold. That is the single
failure the property exists to prevent, so it is not made optional to save four
characters. It is a **breaking change** to a Protocol shipped in M1; the identity and
belief folds declare, and so must any consumer's.

Worth recording that the type checker caught two test folds that declared
`fold = "deterministic"` without the annotation: a bare string infers as `str`, which
does not satisfy the Literal. The requirement is enforced, not merely documented.

## 2. What M1 claimed, narrowed — [R61 clause 2]

M1's fold docstring said flatly *"fold-replay is an invariant (same log, same
projections)"*. True while every fold was arithmetic over the stream; false the moment
one makes a model call. R26's invariant **is not weakened** for deterministic folds —
it simply never governed the other kind — and the docstrings now say which.

## 3. Anchor-replay, and what it deliberately does not claim — [R61 clause 3]

**Same log → the same anchors; the content is regenerated.**

Everything the fold derives *from the log* — which sections exist, the ranges they
cite, where their boundaries fall — is a pure function of the log and replays exactly.
The prose is not, and R61 declines any equivalence claim about it, because nothing
defines semantic equivalence of two consolidations and nothing would enforce it. The
invariant is asserted only where it can be **checked**.

That shaped two things here:

- **Section ids are derived from the range they cover**, not drawn from a counter or a
  clock. Random ids would make every anchor differ across a rebuild while the
  boundaries stayed identical — an invariant true in fact and failing its own test.
- **The test summarizer is deliberately unstable**, stamping each consolidation with a
  generation number. A summarizer that returned the same string twice would make
  anchor-replay pass for the wrong reason: the prose would match by accident and the
  test would prove nothing about which half is invariant. The suite asserts the prose
  **differs** across a cold rebuild for exactly that reason.

**One edge worth naming.** A deliberate re-consolidation — a better prompt, run by
hand — is *not* fold-derived: nothing in the log records that it happened. So it falls
outside the anchor-replay claim, and a rebuild discards it and returns generation 1.
That is a second, sharper reason the carry path exists (§6).

## 4. The grades are separate objects — [R61 clause 4]

Clause 4 forbids a **binding** read of generated prose — any read that *settles*
something: canon, a gate, a validator's verdict. Downstream generation may and must
consume it; that is what the chronicle is *for*.

A rule written down is a rule that gets forgotten under deadline, so the split is in
the API: `Anchors` and `Prose` are separate objects, and neither can be reached from
the other. A caller that needs a verdict takes `Anchors` and finds there is simply no
prose on it to misuse.

Two of clause 4's by-construction guarantees are testable and tested: the **tail comes
from the log, never the chronicle**, so near-term continuity never depends on generated
prose; and supersession keeps the superseded prose readable by id, so what a past run
actually read can still be seen.

## 5. Where the pin key is built — [R61 clause 5]

Clause 5 pins generative folds, and warns that a key of stable identifiers alone
*cannot notice a rewritten prompt*. **That failure was reachable from here**: the fold
can see the event range but not the prompt, so any key the fold built would be blind in
exactly that way — it would serve the old consolidation to an edited one.

So the fold does not build a key at all. The `Summarizer` does, where the prompt is
visible, typically by drawing through `PinnedModel` — which keys the whole request,
prompt included, from M4. The fold records the key it is handed and asserts nothing
about it.

**Two places this library already satisfies clause 5 by other means**, worth recording
as divergences that happen to land ahead:

- Clause 5 wants an identifier tuple to *address* the pin and a request digest to
  *validate* it. `pin_key` is content-addressed — **the key is the digest** — so the
  two-step collapses into one and an edited prompt is a miss by construction.
- Clause 5 widens the key's first component from `turn_id` to a unit-of-work id.
  `PinLedger.unit_id` was **never turn-scoped**; a consolidation pass is simply its own
  ledger. The widening was already there, arrived at for unrelated reasons.

The payoff is asserted end to end in `test_acceptance_m6.py`: a consolidation committed
to the log and rebuilt through `ReplayPins` comes back **bit-exact, with the model
reached zero times**; the same rebuild against a cold ledger returns the same anchors
and different prose.

## 6. The migration asymmetry — [R61, recorded with the ruling]

Every other projection here is disposable: drop it, replay, get the identical thing
back. This one is not. Rebuilding it re-runs every model call it ever made — the whole
cost again — and returns different words, so anything that read the old prose read
something that no longer exists.

Two consequences:

- **`Projector.rebuild` refuses a generative fold** unless `discard_generated=True`
  says so out loud. Its docstring sells rebuild as *"stand up the adapter, replay the
  log"*; for this fold that is false and expensive, and a comment would not have
  stopped anyone.
- **`export` / `load` exist**, which no other projection needed. `load` is idempotent
  so a carry can be resumed, and additive so two chronicles over one log merge without
  either winning.

Carrying grants no authority. A carried chronicle and a rebuilt one are equally valid,
because neither is canon.

## 7. Sections are rows, not events — [ours]

R61 does not rule on this; it follows from the projection framing. A projection's
output does not belong in the log it projects from — that would put R56-grade content
inside R26-grade canon and make canon contain its own derivation. Pins are in the log
because they record nondeterminism *a run consumed*; a section is a read model built
after the fact. Different things.

This reversed an earlier working assumption of mine, which had reached for events on
the grounds that a deterministic rebuild needed them. The premise was wrong: a
fold-generative fold is still a fold, and projection-hood never required determinism.

## 7a. What review round 1 changed

Five findings, all real. Two worth recording rather than merely fixing.

**A generative fold's draw was holding SQLite's write lock.** `Projector._apply_batch`
calls `apply` inside `BEGIN IMMEDIATE` so state and position commit together — free for
a deterministic fold, and badly wrong for one whose `apply` makes a remote call. Every
append in the process blocked for the model's full latency, and a batch with several
boundaries held it across all of them. Measured, not reasoned about: a second connection
got `database is locked` for the whole call.

The fix generalizes, as R61 clause 1 says such things should: `GenerativeFold` adds a
`prepare` phase that the projector runs **outside** the transaction, leaving `apply`
with nothing slow in it. Every generative fold needs it — a portrait regenerator has the
identical problem — so it belongs in `timeline` beside the grade, not in the chronicle.

**And the fix introduced a bug the existing tests caught immediately.** Every `prepare`
in a batch runs before any `apply`, so `consolidated_through()` still reads the old
watermark and two boundaries in one batch both claimed the same start — the second
covering a range the first already had. The pending draws are the missing half of the
answer, and they clear themselves as `apply` consumes them. Both the failure and the fix
now have their own test, since the one that caught it was about lag and would not have
named this.

Three smaller ones, each a real defect:

- **`Consolidation.pin` was unfillable**, which made it decorative. `PinnedModel`
  computes the key internally, so a summarizer using the recommended integration could
  not learn it without duplicating the policy — and a duplicated policy drifts. Fixed by
  giving `PinnedModel` an `on_key` recorder, and by making the field **plural**, which
  is the accurate shape: consolidating a range may take several calls. The acceptance
  test now *asserts* the pins are identical across a bit-exact rebuild rather than
  inferring it from the prose.
- **`covering()` filtered in Python**, loading every section's prose to answer a point
  lookup and bypassing the range index added for exactly that read. Now both predicates
  in SQL, one row back, and `EXPLAIN QUERY PLAN` confirms the index is used.
- **`load()`'s docstring promised a merge it does not perform.** Section ids are
  range-derived, so two chronicles over one log collide on every id and the incoming
  prose is dropped. That is correct for the case the method exists for — resumable
  transfer of *one* chronicle — and the docstring now says so instead of claiming
  "without either winning".
- **`chronicle` was missing from the package root**, so `import anagrapheus;
  anagrapheus.chronicle` did not work and root API discovery omitted the new layer.

## 7b. The first consumer outside this suite — [ours, M8b]

The negotiation demo installs `ChronicleFold` with `RoundClosed` as its unit boundary,
and three things only a real consumer surfaces came out of it.

**The unit vocabulary really is the application's.** A negotiation's unit is a *round* —
not a turn, not a session, and nothing the library could have guessed. `EventTypes` was
enough; no consumer has yet wanted a boundary that is a predicate over payload rather
than a type.

**A generative fold cannot be wired like a pure one.** Every other fold in `anagrapheus`
is constructed with the stores it writes and nothing else. This one needs a summarizer,
the summarizer needs the pin ledger, and the ledger needs the store the composition root
has not opened yet. The demo resolves it by binding the resolver after construction and
raising if a draw arrives before one — see [note 06 §9](06-demo-agent.md). It is the one
fold whose installation is opt-in there, for the same reason: it bills a model call on
every boundary, so a table opened to ask belief questions is not billed for prose nobody
reads.

**§5's rule about where the key is built has a second edge.** Building the key inside the
summarizer is necessary so a rewritten prompt is noticed — that is R61 clause 5. What the
demo added is the converse obligation on the *prompt*: it may not contain an identifier.
A consolidation prompt rendering an entity id, a minted belief id or an event id keys
differently on every run, so every pin misses and the fold silently re-draws. The demo's
renderer therefore reads its four domain events by their human fields and skips the
bookkeeping — which is what makes its acceptance test able to claim the stronger thing:
the whole run replays on a **fresh store**, not the one that recorded it.

**What is still not exercised.** Four rounds is not the case this layer exists for. The
recency/depth split is wired and read, but nothing yet runs long enough for consolidated
prose to be doing work that the verbatim tail could not.

## 8. Open questions

- **Nothing enforces the no-binding-read rule across a process boundary.** The API split
  stops a caller reaching prose through `Anchors`; it does not stop one reading prose
  and then using it to decide something. Clause 4 is a discipline at that point, and
  the library can only make the honest path the easy one.
- **Re-consolidation invalidates replay for runs that read the old prose.** Correct
  behaviour under R56 — the divergence is announced, not silent — but it means
  re-consolidating is a heavier operation than it looks, and nothing currently warns at
  the call site.
- **The default retriever is a placeholder and should stay one.** Named
  `substring_retriever` so it cannot be mistaken for ranked retrieval. The risk is the
  opposite of the usual one: a default that looked competent would discourage replacing
  it.
- **Merging two divergent chronicles is not possible**, and `load` no longer pretends
  otherwise. It would need identity that distinguishes two consolidations of one range,
  which range-derived ids deliberately do not provide — the same property that makes
  anchors replay is what makes divergent sections collide.
- ~~**Tier 3, the live smoke test**, still does not exist.~~ **Built in M7, widened in
  B** — see [note 06 §5 and §12](06-demo-agent.md).
