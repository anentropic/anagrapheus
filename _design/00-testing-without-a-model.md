# Testing without a model

**Created 2026-08-23.** How anagrapheus's test suite gets its model responses, and
why the answer is mostly "it doesn't".

---

## 0. Status

**Proposed when written; all four tiers now exist.** The suite this note planned was
built over milestones 1–7, and the plan survived contact: folds are tested without a
model, the provider seam is tested with `FunctionModel`, generation-replay is tested
from recorded pins, and one live call gates a release. It mints no rulings and binds
nothing.

*Amended 2026-08-29 (M4).* Two things the note asked for are now answered rather than
proposed — see [§8](#8-open-questions).

*Amended 2026-08-31 (M7).* **Tier 3 exists**, six milestones after the other three, and
it gates a release. The delay is worth recording rather than tidying away: tiers 0–2
grew to nearly three hundred tests on synthetic response shapes alone, so until now
nothing in this repository had ever confirmed the stack works against a real provider.
See [note 06 §5](06-demo-agent.md).

Facts with a shelf life are dated in [§7](#7-sources). Model prices and third-party
free tiers move; the reasoning does not depend on their exact values, only on their
order of magnitude.

---

## 1. The requirement

Unit tests must run on every push, in CI, on pull requests **from forks**, at
approximately zero cost, without flaking.

The obvious reading is "find a cheap or free model API to point the tests at". That
reading is wrong, and the rest of this note is why.

---

## 2. Almost no test needs a model

Two properties of the architecture do the work, both from
[the agent-core design](https://github.com/anentropic/narrative-vm/tree/main/_design/agent-core).

**The log is the authority and everything else is a fold over it.** archeion holds
what happened; doxastica, identity, chronicle and the transcript are projections
rebuilt from it. A test for any of them is: append events, rebuild, assert. No model
is involved because no model was ever involved — the projection is a pure function of
the log.

Design doc 00 §4c states this as an invariant rather than an aspiration: *fold-replay
is an invariant — same log, same projections.* A fold that needs a model to be tested
has escaped its station, and the test failing to be writable without one is the
signal.

**The provider owns no state that outlives the call.** It is the one layer that talks
to a model, and doc 00's layer table says it must never own anything persistent. So
the model-touching surface is a single seam, not a property smeared across the
codebase. Everything on the far side of that seam is testable by substituting the
seam.

The consequence: the question is not "what backs our tests" but "what backs the thin
slice of tests that exercise the provider seam itself".

## 2a. The pin ledger is already a cassette

Doc 00 §4 defines **pinning**: recording the result of a nondeterministic effect
under a key, so the effect happens once and everything afterwards reuses the record.
Across runs, *replay looks results up by key instead of re-calling*.

That is record-and-replay, built into the domain, for production reasons that have
nothing to do with testing. Doc 22 §52 names the consequence directly — pinning gives
a replayable corpus.

This is worth more than it first appears. The usual record/replay test rig (VCR
cassettes, HTTP mocks) is **test-only scaffolding**: it can drift from how the system
actually behaves, and nothing catches the drift, because production never runs that
path. Replaying from pins runs the *same* code path production runs. A test built on
it cannot quietly decay into fiction.

So the default fixture format for end-to-end tests is a recorded pin ledger, not a
mock library.

---

## 3. Four tiers

| Tier | Backing | What it covers | Runs | Cost |
|---|---|---|---|---|
| **0** | none | folds: archeion, doxastica, identity, chronicle; fold-replay | every push | $0 |
| **1** | `TestModel` / `FunctionModel` | the provider seam: tool loop, structured-output parsing, pin reuse under retry | every push | $0 |
| **2** | recorded pins; VCR where wire-level fidelity matters | end-to-end runs, generation-replay, divergence detection | every push | $0 |
| **3** | a real model | "does this still work against an actual provider" | nightly / on demand | a few $/month |

Tiers 0–2 are the suite. Tier 3 is a smoke test that lives outside it.

### 3a. Tier 1 — fakes, and why they beat a real model here

[pydantic-ai](https://pydantic.dev/docs/ai/guides/testing/) sits behind the `Provider`
port (named in
[doc 30 §6](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/30-nvm-open-source-extraction.md),
which lists *fakes* among what that layer owns). It ships two:

- **`TestModel`** — calls every tool and generates schema-valid structured data. The
  cheap default: proves the wiring holds without asserting anything about content.
- **`FunctionModel`** — you supply the function that produces the response.

`FunctionModel` is the one that matters, and the reason is not cost. It can produce,
on demand and deterministically, the responses we most need to handle and are least
able to obtain: a malformed structured output, a refusal, a tool-call loop that never
terminates, a response that changes shape between two calls with the same key.

A real model — free or paid — mostly behaves. Testing error handling against
something that keeps succeeding tests nothing. The fake is not a budget substitute
for a real model here; it is the better instrument.

### 3b. Tier 2 — replay

Prefer recorded pins, per [§2a](#2a-the-pin-ledger-is-already-a-cassette).

Reach for HTTP-level cassettes ([pytest-recording](https://github.com/kiwicom/pytest-recording),
`--record-mode=once`) only for what pins abstract away: SDK serialization, wire
format, provider-specific response quirks. Pins record the *semantic* result; they
will not catch an SDK upgrade changing how a request is framed.

If cassettes are added: they capture full requests including `Authorization` headers.
Redact before committing.

### 3c. Tier 3 — the live smoke test

Not part of the suite. Scheduled, or behind a label; never gating a PR.

Rough cost at 2026-08-23 first-party prices — 50 calls/night, ~2K in / 500 out on
Claude Haiku 4.5 ($1 / $5 per MTok): about $0.23/night, ~$7/month. The Batch API
halves it; prompt caching on a stable system prefix cuts the input side further. Call
it $3–4/month, which is the "approximately zero" the requirement asked for.

The specific model is not a commitment — it is behind the `Provider` port precisely so
it can change. Pick the cheapest adequate model at the time.

---

## 4. Making zero provable

```python
# tests/conftest.py
from pydantic_ai import models

models.ALLOW_MODEL_REQUESTS = False
```

This hard-blocks any request to a non-fake model. A stray live call fails the suite
instead of silently billing us.

Belt and braces: do not put a provider API key in the PR workflow's environment at
all. Tiers 0–2 then *cannot* spend money, rather than being trusted not to.

Both halves matter. The flag catches a test that forgot to override its model; the
absent key catches a code path that bypasses pydantic-ai entirely.

---

## 5. Rejected options

**Free hosted tiers** (Groq, Cerebras, Gemini and similar). The intuitive answer, and
the worst fit:

- Repository secrets are unavailable to workflows triggered by pull requests from
  forks. Every outside contribution would fail CI.
- Free-tier rate limits become flaky tests under a busy CI queue.
- The tiers change roughly monthly; Gemini cut its own in late 2025, and Cerebras now
  requires a verified payment method, making it a trial rather than a free tier.
- Most fundamentally: a real model is nondeterministic. That is the property a unit
  test least wants.

**GitHub Models.** Was the natural "free inference in Actions via `GITHUB_TOKEN`"
answer. Fully retired 2026-07-30 — playground, catalog, inference API and BYOK. Noted
because it is a plausible suggestion that no longer works, and someone will propose it.

**Local models on runners** (Ollama, llama.cpp). No dollar cost, but GitHub-hosted
runners have no GPU, so it is slow CPU inference — and the output is still
nondeterministic, so it buys nothing a fake doesn't. Worth reconsidering only if we
specifically want to prove the stack survives a small, weak model, which is a
different question from "do our tests pass".

---

## 6. Tests and evals are different

[Doc 22](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/22-nvm-eval-and-tuning.md)
is an eval methodology. Evals need a real model, cost real money, and exist to measure
model behaviour under change. That is their job.

Unit tests exist to measure *our* behaviour, holding the model fixed or absent.

Keep the budgets and the reasoning separate in both directions: eval spend must not
leak into the suite, and the suite's frugality must not starve the evals. Tier 3 above
is a wiring check, not an eval.

---

## 7. Sources

Volatile facts, with the date they were checked.

| Fact | Checked | Source |
|---|---|---|
| `TestModel`, `FunctionModel`, `ALLOW_MODEL_REQUESTS`, `Agent.override` | 2026-08-23 | [pydantic-ai testing guide](https://pydantic.dev/docs/ai/guides/testing/) |
| GitHub Models fully retired 2026-07-30 | 2026-08-23 | [GitHub docs](https://docs.github.com/en/github-models/about-github-models) |
| Claude Haiku 4.5 at $1 / $5 per MTok; Batch API at 50% | 2026-08-23 | Anthropic first-party pricing |
| Free tiers volatile; Cerebras requires a payment method | 2026-08-23 | [free-tier survey](https://ianlpaterson.com/blog/free-llm-api-2026/) |
| VCR cassettes via `--record-mode=once` | 2026-08-23 | [pytest-recording](https://github.com/kiwicom/pytest-recording) |

Architectural claims cite
[`_design/agent-core/00`](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md)
(§2 the layers, §4 pins and the two replays) and
[`_design/v2/30`](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/30-nvm-open-source-extraction.md)
(the `Provider` port). Those citations are provenance, not prerequisites — every claim
above should stand for a reader who does not follow them.

---

## 8. Open questions

- ~~**Where do recorded pin ledgers live, and in what format?**~~ **Answered in B: the
  same serialization as the log**, and for a reason beyond tidiness. Every event line in
  a recording is a `StoredEvent` exactly as stored, so a recording is upcast on read like
  anything else read from a log and outlives the schema it was written under — a
  test-facing format would need a migration story of its own. One header line says which
  route recorded it, because a pin key includes the model's id. They live in
  `tests/fixtures/`, regenerated by `just record-fixtures`. See `timeline.recording` and
  [note 06 §12](06-demo-agent.md).
- ~~**What is the canonicalization policy for test pins?**~~ **Answered in M4.**
  `CallPolicy` is replaceable and `SemanticCallPolicy` is the default, and the answer is
  the same for tests as for production *because they run the same path* — which was the
  point of preferring pins to a mock library in the first place. Tests do not get a
  looser policy; they get the one the application ships.
  See [note 04 §3](04-provider-layer.md).
- ~~**Does tier 3 gate a release?**~~ **Answered in M7: it does.** `release.yml`'s
  `publish` job needs it, so a broken provider seam stands between the tag and PyPI
  rather than sitting in a notification nobody reads. It runs on `workflow_dispatch`
  and on a release tag, never on a pull request — §5's fork-secrets constraint again.
  See [note 06 §5](06-demo-agent.md).
