# The demo agent, and tier 3

**Created 2026-08-31.** The second consumer doc 00 §10 Q4 asks for, and the live smoke
test note 00 planned six milestones ago and never built. Spec authority is
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

## 2. Higher-order belief is scope naming — [ours]

A scope id is an arbitrary string, so three scopes per party are enough:

| Scope | Holds |
|---|---|
| `party:{id}:held` | what they actually want — private |
| `party:{id}:stated` | what they have said — public |
| `party:{id}:model-of:{other}` | what they believe another party holds |

One three-line function then answers three different questions:

- **`misreading(eu, uk)`** — how wrong we are about them. The agent acts on it.
- **`own_gap(uk)`** — their distance between word and intent. **Nobody but that party
  can ask this**, because nobody else holds their `held` scope. That asymmetry is what
  makes the domain strategic rather than merely multi-party.
- **`taken_at_their_word(eu, uk)`** — whether we believe what they said. Distinct from
  the first: it measures scepticism, not error, and a holder can be *right* to
  disbelieve.

§6a blesses the collision this rests on — revision history keys on the belief id alone
and is cross-scope, and *"for divergence queries that collision is the point."* Three
holders, one belief id, asserted directly because it looks like a bug from outside: if
ids differed per scope, every divergence query would return nothing and report perfect
agreement.

**Why the convention lives in the example and not the library.** §6b argues the
predicate registry belongs in code because it is *schema*. Scope naming is not schema —
it is a modelling choice about how many minds an application has and how they nest,
which the library cannot know.

## 3. The finding: a plan is per belief id, a belief lives in N scopes — [ours]

`plan_rekeys` returns **one plan** for a merged entity's belief. That belief was held in
**three scopes**. The plan cannot say which, because it is computed from the
entity→belief index, which records what was *minted* and is scope-free by construction.
Nothing can enumerate scopes either: `MemoryCore` has `query_scope` and no `scopes()`.

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

## 6. Open questions

- **Does the re-key loop belong in the library?** §3 above states the case both ways
  and deliberately does not decide it.
- **Nothing enumerates scopes.** The application supplies the list, which is fine while
  the parties are known up front and gets harder if scopes are created dynamically.
- **The demo has one round.** The chronicle's recency/depth split is exercised in M6's
  own suite but not here, so a long negotiation — the case the chronicle exists for —
  is still only tested against synthetic units.
- **Tier 3 tests one call.** Structured output, tool calls and streaming against a real
  provider are all still unproven; the pins that cover them are synthetic. Widening it
  costs cents, and the reason not to is that each addition is another thing that can
  fail for reasons unrelated to this library.
