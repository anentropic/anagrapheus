# The provider layer

**Created 2026-08-29.** What `anagrapheus.provider` decided, and where it departs from
or fills gaps in the upstream design. Companion to
[note 00](00-testing-without-a-model.md), which planned this seam before it existed;
spec authority remains
[`agent-core` doc 00 §2 and §4](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md).

Doc 00 §2 gives this layer *the model call: structured outputs, tool loop, casting* and
forbids it **any state that outlives the call**. §4 says what to do with the call: pin
it. Until this milestone the pin ledger had shipped for three milestones and never
pinned a real effect — every test drove it with a synthetic callable, so §4's whole
argument was asserted against a stand-in.

---

## 1. The pin attaches per request — [ours]

Not per agent run. §4a keys on **what consumes the entropy**, and inside one run a tool
loop makes several draws: a request that decides to call a tool, then a request that
reads what came back. Two draws, two keys.

Pinning the run's final answer instead would replay the *conclusion* while re-running
every step that reached it — including the tool calls, which touch the world. The
acceptance test makes the difference concrete: a two-step run replays with the model
reached **zero** times.

`PinnedModel` subclasses pydantic-ai's `WrapperModel`, so it overrides one method and
inherits the rest of the `Model` surface. It drops in wherever a model does, which
means an application's agent code is unchanged by the decision to pin it.

## 2. The timeline gained an async resolver — [ours]

`Model.request` is `async`; `PinResolver.resolve` took a sync effect. So the protocol
gained `resolve_async`, implemented on both `PinLedger` and `ReplayPins`.

It is the same policy with the draw awaited — same key, same reuse, same
concretization, same scratch row — so the two paths **share their internals** rather
than becoming two copies of a rule that could drift apart. The store stays synchronous,
which is right: the slow part is the draw, and the draw is the part being awaited.

One property worth stating because a test depends on it: under strict replay the effect
is *never awaited at all*. The miss raises before the call is made, which is what makes
"no model was reached" an assertion rather than a hope.

## 3. Keying a model call — [ours]

The crux. §4a is explicit — *"the semantic identity of the call, never the serialized
request shape and never how you intend to parse the response"* — and §4c is equally
explicit that the canonicalization policy belongs to **the application**. So `CallPolicy`
is a Protocol with a working default, the third time this library has taken that shape
(after identity's vocabulary and beliefs' predicate registry).

**What `SemanticCallPolicy` drops, and why it must.** pydantic-ai's parts carry a
`timestamp` on every user, system and tool-return part; `id`, `provider_name` and
`provider_details` on text and tool-call parts; and a provider-assigned `tool_call_id`.
Left in, every call would get a fresh key: pinning would appear to work, reuse would
never happen, nothing would raise, and the bill would be the only symptom. That is the
failure mode this note most wants on record.

**What it keeps, including one that looks wrong.** The messages' semantic content, the
model's identity, the available tools, and the required output shape. The last two
deserve their reasons:

- **The tool set** changes what the model can do, so it changes what it draws. An agent
  that gains a tool can answer the same prompt differently, and reusing the old answer
  would hide that.
- **The output shape** is sent *to* the model and changes what it generates, so it
  consumes entropy. This is not §4a's forbidden case: what §4a forbids is keying on the
  parsing done *after* the draw, which is `interpret`'s job and never reaches the
  material.

`model_settings` is deliberately **out** by default. A temperature change arguably makes
a different call and a timeout change plainly does not, and that judgment is the
application's — which is what having a replaceable policy is for.

The default reduces by **exclusion** rather than by listing each known part type.
pydantic-ai adds part kinds between releases, and an allow-list would silently drop a
new kind's content from the key — making two different calls collide, which is the
worse of the two failures and the quieter one.

This also answers the open question [note 00 §8](00-testing-without-a-model.md) left —
*"what is the canonicalization policy for test pins?"* — with the same answer for tests
and for production, because they run the same path.

## 4. Streaming is refused, loudly — [ours]

`request_stream` raises `StreamingNotPinnable` rather than inheriting `WrapperModel`'s
passthrough.

A streamed response arrives in pieces and has no single result to record, so it cannot
be pinned as it stands. The inherited behaviour would delegate to the wrapped model —
silently — putting an **unpinned live draw** inside a run that believes every draw is
pinned. The run would not replay, and nothing would say so. Declining is the honest
version of "cannot"; half-supporting it would be the dishonest one.

## 5. Tools are pinned, and the wrapper is always async — [ours]

§4 lists a tool invocation among the effects worth pinning, and it is the half that
touches the world. Pinning only the model side gives a replay that reproduces what the
agent *said* while re-running what it *did*.

**Which tools to pin stays the caller's judgment**, and it matters in both directions: a
world-reading tool wants pinning, a pure one needs none, and a deliberately live one — a
clock, a current price — must *not* be pinned, because a replay should show the old
answer only where the old answer is what the run acted on.

**`pinned_tool` returns a coroutine function whichever kind it is given.** This was
forced by a real collision, found by the acceptance test rather than by reasoning:
pydantic-ai runs a *sync* tool in a worker thread, and the ledger's SQLite connection
belongs to the thread that opened it — so a sync wrapper reached the store from the
wrong thread and raised.

The obvious fix — opening the connection with `check_same_thread=False` — was declined.
It would loosen the storage contract for every consumer to serve one, and it would be
genuinely unsafe: pydantic-ai runs tool calls **concurrently** when the model requests
several, which would make a shared connection a data race rather than merely a lie.
Instead the wrapper is async, the ledger is touched on the calling thread, and a sync
tool body is offloaded so a slow one still does not block the loop. That split is
exactly what `resolve_async` makes possible.

## 6. What this milestone changed elsewhere

- **pydantic-ai is a required dependency**, moved out of the dev group.
  [Note 01 §2](01-package-shape.md) called `provider` an *optional extra*; it is not.
- **anyio is declared explicitly.** It arrived transitively with pydantic-ai, and this
  subpackage imports it directly — an undeclared import that happens to resolve is a
  dependency you have without saying so.
- **[Note 00](00-testing-without-a-model.md)'s status** said "no code exists yet, so
  nothing here has been validated against a real suite". Four tiers later, tiers 0–2
  exist and are exercised; the note says so, and its canonicalization question is
  answered in §3 above.

## 7. Open questions

- **Streaming.** Refused, not solved. A streamed response could in principle be pinned
  as its accumulated parts and replayed as a synthetic stream — but "replayed a stream"
  and "streamed" are not the same claim, and the difference would need stating before
  the feature would be honest.
- **Does `model_settings` belong in the default key?** Left out on the argument that
  temperature is the application's judgment. A consumer who pins across a settings
  change and gets a stale answer would reasonably call that a bug in the default.
- **Tier 3, the live smoke test**, still does not exist. Note 00 §8 asks whether it
  gates a release; nothing here answers that, and a nightly nobody reads is not a smoke
  test.
- **The pin corpus as a fixture format** — note 00 §8's first open question, about where
  recorded ledgers live and in what format. The acceptance test records and replays
  within one test rather than from a committed fixture, so the question is still open in
  practice as well as on paper.
