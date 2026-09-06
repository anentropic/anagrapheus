# The demo agent, and tier 3

**Created 2026-08-31.** The second consumer doc 00 §10 Q4 asks for, and the live smoke
test note 00 planned six milestones ago and never built. **M8a** corrected what the demo
claimed about higher-order belief (§2); **M8b** built the two-agent, multi-round version
that puts the correction under load (§§7–9). Spec authority is
[`agent-core` doc 00 §6 and §10](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md)
and [note 00](00-testing-without-a-model.md).

§10 Q4: *"The second consumer that tests whether these seams are real; the less it
resembles NVM, the more it proves."*

---

## 1. What choosing the domain exposed — [ours]

The first three candidates — incident response, a transaction ledger, a literature
assistant — were all rejected for the same reason, and it was not that they resembled
NVM. Each had **one doxastic agent holding beliefs about a world**. NVM models several
actors holding beliefs *about each other*, and none of the three would have exercised
that at all.

Checking the code turned the objection into a finding:

- `anagrapheus.beliefs` **does** ship scopes and `divergence(store, holder, reference)`.
- Every use in the library's own suite is `"agent"` against `"world"` — one believer
  measured against ground truth, the degenerate case of a design built for several.
- §6c calls that query *"the whole multi-agent-disagreement case"*, and nothing had yet
  asked it a question with more than one agent in it.

So the demo's first job is not to show the stack off. It is to run §6c's claim.

## 2. Higher-order belief is **not** scope naming — [corrected 2026-09-05, M8a]

**This section previously said the opposite, and it was wrong against a ratified NVM
ruling.** It claimed *"higher-order belief is scope naming"* and shipped a third scope,
`party:{a}:model-of:{b}`, holding what one party believed another wanted. Corrected
forward rather than quietly reworded, because the wrong version is in a merged PR and in
the M7 milestone record.

### What the corpus says

[Doc 24](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/24-nvm-higher-order-beliefs.md)
exists for exactly this question, and §1 opens by naming the shipped design *"the wrong
frame"*:

> **R28 — Higher-order belief is derived, never stored.** RATIFIED 2026-06-17. *"nested
> mental-state beliefs are reconstructed on demand from access-beliefs (R27) and
> discarded — abduction-on-demand applied to minds; **no `BeliefState` ever stores 'X
> believes Y believes …'**."*

Three more govern the shape:

- **R27 — Access is first-class observable content.** RATIFIED 2026-06-17. Actors form
  *ordinary first-order* beliefs about the witness set, *"allowed to diverge from the
  true witness set … The divergence **is** the deception/surprise engine."*
- **R29 — Reconstruction reads only the observer's scope.** RATIFIED 2026-06-17. *"If
  you peeked at B's actual scope you would get the true answer, not A's (possibly wrong)
  model, and false belief becomes unrepresentable."*
- **R30** caps realised depth at 3, raisable per query. **R50** (RATIFIED 2026-07-04)
  keeps derivation to existing core *reads* — `query_scope`, `get_scope_at` — *"not a
  `BeliefStore` op"*, building *"no throwaway core scope."*

### Why the stored version breaks

Not the content — a proposition may mention another mind, and nothing about the record
is special. **The update trigger.** A first-order belief updates when its owner observes
a *world* event; a higher-order belief updates when its owner observes a *meta* event, a
change in what the other actor had access to. A flat stored row is driven by the wrong
one.

Doc 24 §1's case: a guard watches a courier stash a letter, watches the courier *leave*,
then watches a thief move it. The guard's own belief updates on the move; the guard's
belief about the *courier's* belief must specifically not. A stored row gets folded
forward by the world event, *"and deception/surprise silently break."*

The failure is silent and it flatters: a model that folds forward converges on the
truth, so the agent looks perceptive and the misreading being measured quietly goes to
zero.

### In fairness to M7 — the exception it sat inside

R28 carries one, and it applies:

> **Exception:** a belief about another mind with *no witness trail* (a hunch about
> motive — "A thinks C resents her") is a stored **first-order `Inferred` belief in A's
> scope** … first-order content that merely *happens to be about another's
> disposition*, **not** a *derived nested* belief.

`position(uk, fisheries)` is a belief about the UK's **preference**, not about the UK's
belief. The rows M7 wrote were legitimate. What was wrong was **calling them
higher-order**, and building the demo so that the first belief about another's *belief*
would have been stored the same way.

### What replaces it

Two stored scopes per party — `held`, `stated` — both first-order. The third is
`examples/negotiation/nesting.py`: `reconstruct()` gates the log by the observer's own
`witnessed(party, event)` beliefs, folds admitted events into a dict, answers, and is
discarded. `Derived` is deliberately not a `BeliefState` and not scope-shaped, so it
cannot be handed back to `doxastica` even by accident.

`PartyView` (`view.py`) enforces R29 rather than documenting it: reads go through a
guard that raises `ScopeNotVisible` for any scope outside the party's information set —
own `held`, anybody's `stated`. An accidental peek fails loudly instead of answering
plausibly. That is the same *spirit* as note 05's `Anchors`/`Prose` split, but a weaker
guarantee, and §2c below is the whole story of why: this section claimed the stronger
one until review round 1 proved it false.

**Both are asserted mechanically, not read for.** A recording wrapper proves a
derivation calls no mutating op; the guarded reader is asserted to *raise*, and no
attribute of the view to be a bound method of the core. Mutation-tested: making the gate
admit everything (a stored model folding forward) fails four tests including the
guard/courier case, a memoising write fails the no-write test, and re-aliasing
`core.query_scope` fails the R29 test.

## 2a. What R28 costs, which is not nothing — [ours]

A stored model is carried along by a re-key like any other belief. A **derived** one is
rebuilt from the log, and *the log is immutable*: a merge cannot reach into an event
payload, so a remark by `HMG` still says `HMG` after HMG has been merged into the UK.

So the interpreter canonicalizes every party reference at read time, through
`IdentityRegistry.canonical`. Without it the derived model keys on an entity that names
nothing and answers `None` — silently, and only after a merge.

This asymmetry is invisible while nested beliefs are stored. It is a genuine cost of the
ruling rather than a free consequence, and worth stating because the ruling's own
write-up does not mention it: **derived reads must resolve identity themselves.**

A second consequence, smaller: the derived model binds a party *name* to an entity, so
it cannot answer about a party whose identity is unsettled. M7 already learned half of
this — *"reconciling identity is a precondition for briefing an agent, not a tidy-up
afterwards"* — and M8 extends it from the brief to the queries. M7 could ask early only
because both sides of every `divergence` were stored scopes keyed on the same absorbed
entity, which decoupled the scope *name* from the entity and kept the inconsistency
invisible.

## 2b. One simplification, declared — [ours]

Doc 24 §3 step 3 filters the derived scope through *A's model of B's perspective lens*,
carried in the portrait A holds of B. This demo has no portraits — that is NVM's
characterological frame ([07](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/07-nvm-npc-minds.md)),
which the agent-core stack does not own — so **the step is absent rather than faked**.
What remains is the access gate, which is the part carrying R27–R29. The lens is a
fidelity refinement whose own accuracy is still open (U22).

## 2c. Absence of an attribute is not absence of a path — [ours, review round 1]

The first `PartyView` bound `self._query = table.core.query_scope` and the module
docstring claimed it "holds no reference to the belief core at all". **That was false.**
A bound method carries `__self__`, so:

- `view._query(held("uk"), …)` returned another party's private beliefs, and
- `view._query.__self__` *was* the `MemoryCore` — reads and **writes** both, which is
  worse than the review reported.

The test written to prove the claim asserted `not hasattr(view, "core")`. It passed
throughout, because it checked for a *name* while the reachability ran through a value.
The same shape as the M7 error it was meant to prevent: an assertion that looks like
evidence and is not.

Now the view reads through one guarded function that raises `ScopeNotVisible` for any
scope outside its information set — own `held`, anybody's `stated`. Mutation-tested:
restoring the alias fails the test.

**"Unwritable" was also the wrong word**, and the docstring no longer uses it. Python
cannot make a value unreachable — the guard closes over `query_scope` and
`read.__closure__` still finds it. The claim that survives is narrower and true: every
*ordinary* read is checked, and a mistake fails loudly instead of answering plausibly.
Deliberate introspection defeats it, and that is fine, because this was never security
(§2 above) — it is a constraint on the reasoning path.

## 2d. Review round 2 — a lesson applied by halves, and a test that proved its own negation — [ours]

Five findings, all real, and two of them sharper than the round-1 one.

**The depth-2 test proved the opposite of its name.** `test_second_order_is_not_reducible_to_first`
asserted that depth 1 and depth 2 both answered `"concede"` — i.e. that the levels
*agree*, which is reducibility. It used a side remark, and a remark names who overheard
it, so the modelled UK was handed the access-belief that made level 2 fill. **Deleting
the recursion entirely passed all eleven tests.** Now the evidence is a `PositionStated`,
which carries no access-belief: depth 1 answers, depth 2 is empty, and the collapse
mutant fails. A second test keeps the remark case, so the *reason* the two differ is
visible rather than incidental.

Three times now the same failure has appeared in this milestone: an assertion that reads
like evidence and constrains nothing. §2c was the second. This is the third, and it was
in the test for the tower — the one piece of M8a with no consumer yet exercising it,
which is exactly where a weak test survives longest.

**The clause reference was never canonicalized.** §2a above was written about precisely
this — a merge re-keys stored beliefs but cannot reach into an immutable event payload,
so a derived read must resolve identity itself — and the implementation applied it to
the *party* reference only. Clauses are identity entities too (`Table.table_clause`
establishes one). After a clause merge the stored position moved to the survivor while
the interpreter kept minting the absorbed id, so the two stopped intersecting:
`misreading` reported perfect agreement and a brief line read `unknown`. Silently, and
only after a merge. Writing the lesson down did not make it applied.

**A view was half-live.** `dict(table.seats)` and `dict(table.clauses)` froze at
construction while belief and log reads stayed live, so a `Negotiator` built before a
party was seated raised `KeyError` for a name the table knew and `others()` omitted it.
Now read-only live mappings. A negotiation that seats a party mid-round is ordinary.

**And two claims were still overstated** after §2c had already retracted the same
overstatement elsewhere: `nesting.py` said the view "cannot reach another party's
private scope at all", and §2 above said it "holds no reference to the belief core".
Both corrected to the guarantee that actually holds.

All three code fixes are mutation-tested. Two of them — the clause and the live
mappings — initially had no failing test, which is how it was found that the fixes were
unverified rather than merely unproven.

## 2e. The fourth instance, and the shape it always has — [ours, M8b]

`record_reading` writes what one party concluded into a scope. Swapping `held(party)`
for `held(reading.about)` — the EU writing its read of the UK into *the UK's private
beliefs* — **left all 338 tests green**, including the M8 acceptance test with its four
rounds and its second-order assertions. Every subsequent `misreading` and `own_gap`
would then have been measuring something the EU had put there itself.

That is the fourth time in this example. §2c was the first, §2d the second and third.
The shape has been identical every time:

- the claim is stated in a docstring, clearly and correctly;
- the test named after the claim asserts something adjacent that happens to hold;
- the mutant that breaks the claim passes the whole suite.

What catches it is never reading. It is asking, for each load-bearing sentence, *what
line would I change to make this false* — and then changing it. Everything in M8b was
checked that way: nine mutants, and the one that survived is the one written up here.

## 3. The finding: a plan is per belief id, a belief lives in N scopes — [ours]

`plan_rekeys` returns **one plan** for a merged entity's belief. That belief is held in
**as many scopes as there are holders**. The plan cannot say which, because it is
computed from the entity→belief index, which records what was *minted* and is scope-free
by construction. Nothing can enumerate scopes either: `MemoryCore` has `query_scope` and
no `scopes()`.

*(M8a note: this used to say "three scopes" — two per party plus a `model-of`. The
`model-of` scope is gone, but the finding is unchanged and if anything sharper, because
the scopes that remain are all genuinely first-order. What a re-key must now also carry
is the **access-beliefs** the derived model is built from: R27 makes those ordinary
first-order beliefs precisely so they ride the normal pipeline, and a test asserts a
merge moves them.)*

With one scope this is invisible. With several it is a loop, and
`examples/negotiation/rekeying.py` is that loop.

**Two things it has to decide that the library deliberately does not:**

- **Which scopes.** Supplied by the application, because only it knows how many minds it
  modelled. A scope that never held the belief is skipped rather than written with a
  default — an absence is not a disagreement, the same distinction `divergence` draws.
- **Which successor, after a split.** `BeliefRekey.sole()` *raises* when a decision left
  several candidates, and that is the library being right: which product a belief
  belongs to depends on what the belief says, which neither the belief layer nor
  identity may interpret. Without a chooser an ambiguous belief is reported
  `undecided` — never guessed at, never silently dropped.

**Should the loop move into the library?** Recorded, not settled. For: every consumer
with more than one holder writes it identically. Against: it needs the scope list, the
scope list is the application's modelling choice, and a helper would have to take it as
an argument anyway — at which point it is three lines of `for` around a call the
consumer could write. The evidence for deciding is now in the repository rather than in
anyone's head.

## 4. Two consumer traps worth recording — [ours]

Both cost real time here, and both are quiet:

- **`BeliefWriter.revise` hands back the minting event and leaves appending it to the
  caller.** Drop it and the belief is held but never indexed, so `plan_rekeys` cannot
  see it and a later merge appears to succeed while leaving the belief keyed on an
  entity that names nothing. `Table.believe` pairs the two, and a test asserts the
  failure directly.
- **Beliefs name entities by UUID, so a party must be an identity-layer entity**, not a
  string. Obvious in hindsight and not obvious while writing: the first draft used bare
  names and failed at the pydantic boundary. It is also *why* a party can be merged at
  all.

## 5. Tier 3 — [ours, closing note 00 §8]

Six milestones overdue while tiers 0–2 grew to nearly three hundred tests.

**It gates a release.** Note 00 §8 asked and answered its own objection: *"a nightly
that nobody reads is not a smoke test."* `release.yml`'s `publish` job now needs it, so
a broken provider seam stands between the tag and PyPI rather than sitting in a
notification. It runs on `workflow_dispatch` and on a release tag — **never on a pull
request**, because §5 records that fork PRs cannot see repository secrets, which is the
same constraint that rejected free hosted inference as a CI strategy.

**What it asserts is wiring, not quality**, per §6's tests-vs-evals line. Nothing
inspects what the model said.

**Why tiers 1–2 cannot replace it.** Every recorded pin in this repository was produced
by a `FunctionModel`, so every canonicalization test has only ever seen synthetic
response shapes. This is the one place a pin is recorded from a real provider, and
therefore the only place a policy bug that real shapes trigger can surface. That is the
assertion no fake can make, and it is why the test exists at all.

`claude-opus-5`, the documented default, **not downgraded for cost**: roughly 500 in and
200 out is about **$0.0075 a run**, nowhere near note 00's "a few $/month", so a cheaper
model would buy nothing and would test something a consumer is less likely to be using.
Skipped rather than failed without a key, so a contributor never meets a red test they
cannot fix.

## 5a. A gate that silently skipped the new code — [ours]

Worth recording because it is not a Python problem and will recur on any repository
using pre-commit-style hooks.

`prek run --all-files` means **all files git knows about**, not all files on disk. The
whole of `examples/` and `tests/negotiation/` was still untracked when the gates were
run, so ruff checked none of it, reported ten green hooks, and the eighteen violations
surfaced only once CI ran over the committed tree.

The failure mode is specific and nasty: the gate does not warn that it skipped anything.
It reports success over a smaller set than the reader assumes, and the assumption is
exactly the one the flag's name invites.

**The habit that avoids it**: stage new files before running the gates, or run the
underlying tool over explicit paths (`uv run ruff check examples/ tests/ src/`) when the
change adds directories. The second is the one that would have caught this, because it
names the paths rather than trusting a flag to find them.

## 5b. What review round 1 changed

Six findings, all real. **The first invalidated this milestone's headline claim.**

**Tier 3 could never have reached a provider.** `tests/conftest.py` sets
`ALLOW_MODEL_REQUESTS = False` at import, session-wide, so a stray call in tiers 0–2
fails rather than silently billing anyone. That guard is right — and it silently
disarmed the one test written to defeat it. Proven rather than reasoned: with a dummy
key the call raised `RuntimeError: Model requests are not allowed`, never reaching
Anthropic. After lifting the guard for that test alone, the same dummy key produces a
`401` from the provider, which is what proves the request now leaves the process.

The shape of this failure is the one tier 3 exists to prevent, arriving through tier 3
itself: a claim that the stack works against a real provider, with nothing having
checked. It would have been invisible — green in CI (skipped, no key) and confusingly
red anywhere a key existed.

**A green tier 3 run did not mean the smoke test ran.** The test skips without a key,
so a dispatch with a missing or unavailable secret exited successfully having called
nothing. The workflow now fails explicitly when the key is absent, because a gate that
passes by doing nothing is worse than no gate — it is believed.

**And the re-key loop fell into the trap §4 documents.** It wrote survivors straight to
`MemoryCore`, which drops the stance to its default *and* emits no `BeliefMinted`, so
the successor never reached the entity index and a second merge could plan nothing for
it. The note warning about exactly this was written two sections above the code that
did it. Now applied through the recorded minting path, carrying the original stance,
with tests for both halves — a re-key of a re-key, and a hedged belief that must not
come back certain.

Three more, each a real defect:

- **Plans were collapsed by belief id.** `plan_rekeys` emits a plan per identity
  decision, so a proposition naming two independently re-keyed entities yields two
  plans sharing an old id, and a dict kept only the last — a belief that looks re-keyed
  and is half wrong. Each plan is now diffed against the original and the substitutions
  applied together, so arrival order cannot matter.
- **The brief read the first belief in a scope**, ignoring both the clause and the
  entity ids. Correct while a scope held one thing; the moment it held two, the agent
  would negotiate confidently about the wrong proposition. Fixing it immediately
  exposed a modelling error in the acceptance test — beliefs minted under the
  delegation while the brief asked about the party — which the loose lookup had been
  hiding. The story now reconciles identity *before* the round, which is truer anyway:
  you cannot brief an agent about a party whose identity is unsettled.
- **`Table.open` installed only the identity fold**, so every entry point re-assembled
  the projector to add the belief index. That duplication was the smell; both tests now
  patch nothing.

## 7. Reaching around the library — [ours, M8b]

Three of doxastica's operations are used in this example and wrapped nowhere in
`anagrapheus`:

| call | what it buys here |
| --- | --- |
| `add_edge(reading, premise, DERIVED_FROM)` | why a conclusion was written |
| `get_impact(premise)` | what a withdrawn bluff compromises |
| `get_scope_at(scope, event)` | what a party believed at a round's close |

**The direction is not arbitrary and cost a reading of the source to get right.** The
core stores edges dependent → source and `get_impact` walks *against* the arrows, so
`add_edge(conclusion, premise, …)` is what makes retracting the premise report the
conclusion. Reversed, the query comes back empty and every test but one still passes.

NVM **R50 (RATIFIED 2026-07-04)** blesses the read half outright — derivation is
orchestration over `query_scope` and `get_scope_at`, both reads. The write half
(`add_edge`) is not covered by any ruling and is used here on the demo's own judgment.

**This stays a finding rather than a fix.** Widening the library on the say-so of its
only consumer is how a library grows a surface nobody else asked for, and note 06 §3
already declined the same invitation for the re-key loop. The right time to wrap these
is when a *second* consumer wants them; the wrong time is now, when one does.

## 8. What a round turned out to require — [ours, M8b]

Four things the single-turn demo never had to decide.

**Simultaneity.** Every party proposes before any offer is recorded. Record as you go —
the obvious implementation — and the second party negotiates against a counterpart whose
move it has already seen. That is not a round; it is two half-rounds, and it silently
deletes the uncertainty the whole demo is about.

**Access on public events.** `PositionStated` gained a `heard_by` field, because a
derived mind that learns *what was said* and nothing about *who else knows it* cannot
model a third party. Without it the tower stops at depth 1 over public statements and
only an overheard remark can carry the next rung — a limit of the event's shape rather
than of the design. It needed no upcast for one reason worth stating rather than
assuming: no `PositionStated` had ever been persisted, because M7 defined the event and
never emitted it.

**A schema, not a prompt, is what forbids a nested belief.** The update call's output
type has a `Literal` predicate naming `position` and `red_line`. There is no predicate
that says *what they believe*, so R28 is enforced by the type. A prompt asking a model
not to store something is a request; a type it cannot express is a wall. This is also
the constructive half of the M8 correction — R28's own exception is a disposition held
with no witness trail, and that **is** an ordinary first-order belief, so M8b writes
exactly the rows M8a was careful to say were legitimate.

**A public statement re-buries a leak.** The fold is last-evidence-wins, so a party that
states a position after letting something slip is modelled on the statement again. Doc
24 §4 is explicit that this is allowed — a model of another mind is *"deliberately
best-effort"* and the core's revision operator stays out of it — but it is why a leak
has to be *acted on* in the round after it lands, and why the demo runs four rounds
rather than three.

## 9. The composition root's one ordering knot — [ours, M8b]

`Table.open` constructs the chronicle fold; the fold's summarizer draws through the same
`PinLedger` as the agents; that ledger needs the store the table has not opened yet. So
`RoundSummary` takes its resolver *after* construction, and drawing before it does
raises rather than quietly making an unpinned call.

Every other fold in this library is pure and has no such dependency. The generative one
has, and the knot is what the fold that spends money costs. Two smaller consequences
fall out of the same place:

- **The chronicle fold is opt-in and every other fold is not.** Note 06 §5a records that
  a half-wired `Table.open` was a smell; this is the exception, and the reason is that
  installing this fold bills a model call on every `RoundClosed`. The *store* is always
  present; the *fold* is asked for.
- **A consolidation prompt may not contain an identifier.** A pin keys on what the model
  is asked (§4c), so a prompt carrying an entity id, a minted belief id or an event id
  keys differently on every run and every pin misses. The renderer therefore reads the
  domain's own four events and skips the bookkeeping — which also happens to be the
  better summary. This is what makes the acceptance test's real claim possible: the
  whole negotiation replays **on a fresh store**, not the one it recorded into.

## 10. Open questions

- **Does the re-key loop belong in the library?** §3 above states the case both ways
  and deliberately does not decide it.
- **Nothing enumerates scopes.** The application supplies the list, which is fine while
  the parties are known up front and gets harder if scopes are created dynamically.
- ~~**The demo has one round, one agent, and no pinned tool.**~~ **Closed by M8b.** Four
  rounds, two agents, two pinned calls each, a pinned tool in the loop and `RoundClosed`
  as the chronicle's unit boundary — note 05's first consumer outside its own suite.
- ~~**Nothing here forms a belief about another party's *belief*.**~~ **Closed by M8b.**
  The demo now turns on one: the UK lets something slip in round one and believes it went
  unheard, so its model of the EU's model of the UK reads `hold_firm` while the EU's own
  model reads `concede`. Both derived from the same log, differing only in which access
  beliefs gate them, and neither ever stored. The claim available today is the one M8a
  could not make — deriving it is load-bearing, not merely possible.
- **The chronicle is consumed but not yet *needed*.** Four rounds consolidate to four
  sections and the sections reach the next round's prompt, which is more than M6's own
  suite exercised. It is still not the case the chronicle exists for: nothing here runs
  long enough for the recency/depth split to be doing real work rather than being wired
  correctly.
- **A round is pairwise, and says so by raising.** With three parties a round has a shape
  this code has not decided on — simultaneous pairwise briefs, or one offer to the table
  — and guessing would have shipped a plural case nobody thought about.
- **`--live` is unexercised by any test.** The flag swaps the scripted counterpart for
  `AnthropicModel` and the import is lazy, so an offline run never needs the SDK
  configured — but nothing in tiers 0–2 can run it, and tier 3 deliberately does not,
  because a four-round negotiation against a real provider is fifteen calls a release
  rather than one. So the claim available is that the *seam* is right (the model is a
  parameter and everything downstream is pinned), not that a live run works. Somebody
  has to type it.
- **The scripted counterpart decides from the brief, which is a smaller claim than it
  looks.** It reads one line of the brief and branches on it, so changing a belief
  changes an offer and the demo is not a fixture. It is still not a model: nothing here
  shows that a *real* model given this brief would use the third line at all, which is
  an eval question and note 00 §6 keeps evals out of the test suite.
- **Tier 3 tests one call.** Structured output, tool calls and streaming against a real
  provider are all still unproven; the pins that cover them are synthetic. Widening it
  costs cents, and the reason not to is that each addition is another thing that can
  fail for reasons unrelated to this library.
