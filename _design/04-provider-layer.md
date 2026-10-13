# The provider layer

**Created 2026-08-29. Amended 2026-08-29 for M5 (streaming).** What
`anagrapheus.provider` decided, and where it departs from or fills gaps in the upstream
design. §4 states the opposite of what it originally said; the reversal is recorded in
place rather than rewritten away, because what M4 got wrong is part of the argument.

Companion to [note 00](00-testing-without-a-model.md), which planned this seam before it
existed; spec authority remains
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
gained `resolve_async`, implemented on both `PinLedger` and `ReplayPins`. M5 added a
third, `resolve_stream` — see §4 — for an effect whose result exists only once the
*caller* has consumed it.

It is the same policy with the draw awaited — same key, same reuse, same
concretization, same scratch row — so the two paths **share their internals** rather
than becoming two copies of a rule that could drift apart. The store stays synchronous,
which is right: the slow part is the draw, and the draw is the part being awaited.

**Concurrent draws coalesce per key, which the async path made necessary.** `resolve`
has no suspension point between checking the scratch and writing to it, so within a
thread it is atomic; `resolve_async` put an await in exactly that gap. Two concurrent
callers on one key then both missed, both ran the effect, and the second write hit the
primary key — breaking the single promise the layer makes. A lock **per key** fixes it
without serializing unrelated draws, which matters because pydantic-ai runs non-barrier
tool calls concurrently: that is the case the layer is for, not an exotic one.

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
model's identity, and the whole of the request parameters — everything the model is told
it may do and must produce. The last deserves its reason:

- **The output shape** is sent *to* the model and changes what it generates, so it
  consumes entropy. This is not §4a's forbidden case: what §4a forbids is keying on the
  parsing done *after* the draw, which is `interpret`'s job and never reaches the
  material.

The parameters are reduced by **exclusion**, like the message parts and for the stronger
version of the same reason — see §8.

~~`model_settings` is deliberately **out** by default.~~ **Amended 2026-10-12: settings
are in, less the ones about delivery.** The original argument was that a temperature
change arguably makes a different call and a timeout change plainly does not, so the
judgment was left to the application. But the default is what most applications run,
and a consumer who pinned across a temperature change and got the old answer back would
rightly call that a bug — the quiet failure, the one this note ranks worse. So
`CallPolicy.material` now takes the **effective** settings (the wrapped model's own
merged with the run's, which is what the draw is made with), and `SemanticCallPolicy`
keys them by exclusion: `timeout`, `extra_headers`, `service_tier`, `cache` and any
provider-prefixed cache setting are dropped as delivery; everything else, including a
setting pydantic-ai adds later, is kept. When nothing survives, the material has no
`settings` entry, so a call with no draw-changing settings keys exactly as before and
every committed corpus still replays.

The default reduces by **exclusion** rather than by listing each known part type.
pydantic-ai adds part kinds between releases, and an allow-list would silently drop a
new kind's content from the key — making two different calls collide, which is the
worse of the two failures and the quieter one. Tool lists are sorted by name, because
declaring the same capabilities in another order is the same capability set and the
model is not told the order.

This also answers the open question [note 00 §8](00-testing-without-a-model.md) left —
*"what is the canonicalization policy for test pins?"* — with the same answer for tests
and for production, because they run the same path.

## 4. Streaming is pinned, not refused — [ours, amended 2026-08-29]

**This section originally said the opposite.** M4 had `request_stream` raise
`StreamingNotPinnable`, on the argument that a streamed response arrives in pieces and
has no single result to record. The first half of that argument was right and the
conclusion was wrong.

**What the refusal actually cost.** Three surfaces route through `request_stream`, not
one: `run_stream()`, `run_stream_events()`, and an `event_stream_handler` passed to an
ordinary `run()`. So a pinned agent could not report progress *by any means* — the last
case especially, where the caller never asked for a stream at all and only wanted to
observe. Only `agent.iter()` survived, at step granularity. M4's note named just
`run_stream`, which understated it.

**What makes it pinnable.** `StreamedResponse.get()` builds the accumulated
`ModelResponse` and stamps it with a `state` — `complete`, `incomplete`, `suspended` or
`interrupted`. That is the single result the refusal assumed did not exist. Pin it, and
replay rebuilds a stream from it.

**A stream's key is the key its non-streamed twin would have.** §4a keys on the semantic
identity of the call, and streaming is how a response is *received*, not part of what
the model is asked. So one corpus serves both paths, in both directions — a pin recorded
streaming replays through `request`, and a pin recorded plainly replays as a stream.
Both directions are tested, because that decision is exactly the kind that stays true in
name while quietly becoming false.

**An abandoned stream still pins.** If the consumer breaks out early or the stream
raises, what arrived is recorded with the state it ended in. This replaces the open
question M4 left — *"error on an abandoned partial stream"* — with a better answer:
recording nothing would make the next attempt re-run the effect, which is the one thing
the layer promises against, and recording it as `complete` would hand back a truncated
answer dressed as a finished one. A partial is an outcome the model produced.

**What replay does not reproduce: chunk boundaries and timing.** They were the
provider's, they are not in the pin, and synthesizing plausible ones would fabricate a
stream that never happened. Each part is surfaced whole. So a replay *replays a stream*,
which is not the same claim as having streamed — the distinction matters for exactly one
consumer, code that acts on chunk boundaries, and for that consumer the honest answer is
that this is not a recording of its stream.

**`StreamingNotPinnable` is gone**, since nothing raises it any more. An exception no
code can produce is worse than no exception at all, so it was removed rather than left
as a decoration. `ProviderError` remains as the base.

## 4a. One gate, and why it cannot be a lock — [ours]

Two constraints met here, and the second was found by review rather than by reasoning.

**It cannot be an `anyio.Lock`.** `resolve_stream` holds its claim across a `yield`,
which `resolve` and `resolve_async` never do. A Lock is **task-affine** — it refuses a
release by a task that did not acquire it — and a stream's consumer may finalize it from
a different task than opened it, which pydantic-ai's `stream_text(delta=True)` does by
grouping deltas temporally. Held across the `yield`, it raises `RuntimeError` on the way
out, *after* the answer has been delivered. So the guard is an `Event`, which has no
owner.

**And it must be the same gate the plain path uses.** The first draft gave
`resolve_stream` its own gate and left `resolve_async` on its lock — two guards, each
correct on its own path, and a hole exactly between them. Because §4 makes a streamed
call and a plain one **share a key**, a `request()` beginning while a stream is open
misses, both draw, and the live ledger's second write hits the scratch primary key. In
record-mode replay it failed silently instead: no primary key to raise, so the last
completion simply won. (Record mode now draws through a ledger — §7b — so it would hit
the same key.)

The shape of that mistake is worth keeping. Each guard was written to close a race, and
each did close the one in front of it; what neither could see was that the *decision to
share a key* — the thing §4 argues for — turns two safe halves into one unsafe whole. A
design choice made in one section became a concurrency obligation in another, and
nothing connected them but a reviewer.

So both async shapes now claim through one per-key `_inflight` gate: a caller waits for
whatever draw is in flight, however it is drawing, then re-checks and reuses its pin. If
the in-flight draw failed before pinning, the waiter becomes the one that draws. Tests
cover the mixed plain/stream race in both ledger and record mode, and both fail against
the split-gate version.

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

## 7. What review round 1 changed

Five findings, all real, and two of them serious enough to be worth recording rather
than merely fixing.

**Concurrent draws ran the effect more than once** — §2 above. Introduced by this
milestone's own async path, and it broke the layer's central promise. The reproduction
is four lines: `asyncio.gather` two pinned calls on one key and watch the effect fire
twice, then watch the second write hit the primary key.

**The key material was cherry-picked, twice over.** Tools were keyed by *name*, so a
tool that kept its name while changing its description or argument schema shared a key
with the old contract; and whole parameter fields — native tools, tool visibility, the
instructions — were omitted outright. Both replay an answer produced under different
terms, and neither says anything. The fix is the same exclusion principle already
argued for message parts, and the earlier draft's failure is the argument for it: naming
what matters got it wrong the first time it was tried.

Three smaller ones, each a real defect:

- **`RunContext` was detected by parameter name.** pydantic-ai injects it by the first
  parameter's *annotation*, so a context named `state` would have leaked per-run state
  into the key (every replay misses), and an ordinary model argument named `context`
  was dropped (two different calls collide, and the second gets the first one's answer —
  the worse direction, since it answers rather than re-drawing).
- **A plain callable returning an awaitable** — a shape pydantic-ai supports — had its
  coroutine object recorded rather than awaited, so wrapping such a tool changed its
  behaviour, which is the one thing a wrapper must not do.
- The default's structure changed shape as a result, so two tests that asserted on the
  key's *layout* now assert on its *behaviour*, which is what they meant.

And one the fix exposed rather than caused. The older context test wrote its context as
a bare `object()` annotated `object`, and passed locally while failing on CI: CPython had
freed the first instance and reused its address, so `fallback=repr` gave both draws the
same string. It was asserting the *name-based* contract all along and only the allocator
hid it. Both context tests now use values that are distinguishable by construction, and
both fail under mutation — which is the property a test of an exclusion rule needs, since
the failure mode it guards against is two things looking identical.

## 7a. What review round 2 changed (M5)

One finding, and a real one: **the two async paths did not coordinate with each other**
— §4a above. Reproduced before fixing, with the effect running twice and the second
write raising `UNIQUE constraint failed: pin_scratch.unit_id, pin_scratch.pin_key`.

Worth recording that this is the second time in this layer that a *correct local
decision* opened a race one level up. M4's was `resolve_async` putting an await in the
gap `resolve` did not have; M5's was two guards that were each right about their own
path. Both were invisible from inside the change and obvious from outside it.

## 7b. Record mode draws into a ledger — [ours, 2026-10-13]

Record-mode `ReplayPins` used to keep its fresh draws in its own memory: no scratch row,
no commit, and at most one `ReplayDiverged` if `announce` was wired. So a record-mode
run — the demo's one-role lineup swap, for instance — consumed nondeterminism the canon
never held, and its own log could not replay it. The module's claim, "a new run
continuing the same history, announced", was only half true.

Now `on_miss="record"` requires `into=PinLedger(...)` and is refused without it. A miss
draws through that ledger, so the draw lands in its scratch; a hit is adopted into the
same scratch with `PinLedger.adopt`, so the ledger holds the whole new run, not only the
part that differed. `commit()` promotes the pins and a `ReplayDiverged` for the first
miss in one transaction, which makes `announce_to` optional, and the new log replays the
new run strictly. The scratch also gives record mode the live ledger's persist-first
crash behaviour. Strict mode refuses `into=`: it never draws.

## 8. Open questions

- ~~**Streaming.** Refused, not solved.~~ **Closed by M5** — pinned as the accumulated
  response, replayed as a synthetic stream, with the "replayed a stream is not streamed"
  caveat stated in §4 rather than left implied. The partial-stream question it carried
  is closed too, and differently than expected: a partial is pinned, not refused.
- ~~**Does `model_settings` belong in the default key?**~~ **Closed 2026-10-12: yes,
  less delivery settings** — see §3. A custom `CallPolicy` gains a fourth parameter,
  `settings`.
- ~~**Tier 3, the live smoke test**, still does not exist.~~ **Built in M7, widened in
  B.** It gates a release, and runs four checks — plain, structured, a forced tool call,
  a stream — on every route with a key. See [note 06 §5 and §12](06-demo-agent.md).
- **Does a replayed stream need to be paced?** It is delivered as fast as the consumer
  pulls. That is right for a test and for a debugging replay, and it is right for the
  Narrator case that motivated M5, where a replayed turn has no perceived latency to
  hide. It would be wrong if a replay were ever shown to a player as though live — and
  nothing currently stops that.
- ~~**The pin corpus as a fixture format.**~~ **Answered in B** — the log's own
  serialization, committed under `tests/fixtures/` and replayed strictly from disk. See
  note 00 §8.
