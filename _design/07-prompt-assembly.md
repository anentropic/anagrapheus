# Prompt assembly — design (M9)

**Created 2026-10-12. Status: design for review, nothing built.** This note proposes
what `anagrapheus` should own of "what a model is shown", and what it should leave to
the application. Spec authority is
[`agent-core` doc 00 §1–§2](https://github.com/anentropic/narrative-vm/blob/main/_design/agent-core/00-agent-core-architecture.md):
the transcript is derived from the log, and never the reverse. Doc 00 draws the
transcript and prompt assembly box in the §2 diagram and then says nothing else about
it. It is the only layer in that diagram without a contract.

It has two named customers, and the design is measured against both:

- **NVM's brief assembly.** The Pacing Arbiter builds a `TurnBrief` for the Thinker
  deterministically ("not an LLM", NVM 02 §6). The brief has slots for binding
  constraints, a skill index, advisory pulls, retrieved context with the raw
  recent-turn tail, and the speculative cache. It is rendered into three labelled
  registers: "INVARIANTS, must hold", "CONSIDERATIONS" and "CRAFT, load on relevance"
  (NVM 02:376–383). Its exit test is "brief assembly golden-tested against fixture
  log" (NVM 29:953).
- **NVM's Narrator firewall, R24 (RATIFIED 2026-06-12, not amended since).** "The
  Narrator receives the contract and the continuity window. Nothing else. No brief,
  no world state, no corpus, no beliefs, no speculative cache" (NVM 08 §6). It is
  enforced as "a dependency rule, not just a prompt rule", with import-linter on the
  `narrator` package (NVM 21:76–79).

The demo is the third consumer and the one that has already been bitten. Its lessons
are in §1.

---

## 1. What the demo learned the hard way — [ours]

The negotiation assembles every prompt by hand, from string helpers: `brief_for`,
`_observed` and `_context` in `examples/negotiation`. Four defects or near-misses
came out of that, and each one is a design input:

- **The firewall held on one path and broke on the one below it.** `PartyView` made
  R29 structural for beliefs, so a party cannot read another's private scope. The
  chronicle's shared prose then rendered `SideRemarkOverheard` into every party's
  prompt, including the speaker's, who believes nobody heard it
  ([note 06 §9a](06-demo-agent.md)). Nothing failed, because the scripted counterpart
  ignores prose. **A firewall on the readers is not a firewall on the prompt, unless
  the prompt can only be built from the readers.**
- **Canon and generated prose looked alike.** Chronicle sections are advisory (R61
  clause 4, PROPOSED). Woven in beside belief lines, they would be indistinguishable
  from canon, so `_context` labels them by hand. **The register a piece of text
  belongs to is structure, not wording.**
- **Lookups by "the first belief in the scope" answered the wrong question once the
  scope held two** ([note 06 §5b](06-demo-agent.md)). That is a reader bug rather than
  an assembly bug, but it shows up in assembly.
- **A cached brief went stale** ([note 06 §2d](06-demo-agent.md)). `Table.brief()`
  returns a fresh object on every call for this reason. **Assembly reads at a
  position, and has to say which.**

## 2. What the library owns, and what it does not — [ours]

Doc 00 §8 again: *the library ships the shape, the application owns the name*. Here
the shape is how a context is put together, and the names are everything in it.

| The library | The application |
|---|---|
| reading at one position, through grants a role declares | which roles exist, and what each is granted |
| parts that carry a register, a rank and their sources | the slots (`binding`, `technique`, …) and their content |
| a budget pass that never sheds binding parts and reports what it shed | the token counter, the total, salience |
| determinism: same log and same position give the same context | rendering parts into messages (NVM 21:909: "the *structure* of `TurnBrief` is fixed here, its *rendering* is not") |
| a golden-test helper over a fixture log | the `TurnBrief` and `RenderContract` schemas, continuity window sizing |

**It is not a chat transcript.** No consumer accumulates message history across
turns. NVM's Thinker makes a fresh call each turn, and a repair is "a *fresh call*
with the tightened brief" (NVM 29:573). Every demo call is a single shot. A tool loop
inside one call is pydantic-ai's, and it is already pinned per request. Message
history as a projection stays out until a consumer needs it (§7).

**It is not retrieval.** `Retriever` is already a port (note 05), and NVM keeps
ranking in its `retrieval/` module (NVM 21 §7b). An assembler consumes a ranked
result; it does not produce one.

## 3. The shape — [ours]

### 3a. Grants: the only way an assembler reads

An assembler is a pure function from a **grant** to parts. A grant is a frozen bundle
of read-only slices, all opened at one log position:

- `LogSlice`: events up to `upto_seq` that an `admit(envelope)` predicate accepts.
  This is where R58's class gate goes. The application reads `event_class` from its
  own models; see "R58" under §6.
- `BeliefSlice`: named scopes only, read **as of** the event at `upto_seq`. doxastica
  already supports that read (`get_scope_at`), so a fold that catches up during
  assembly cannot tear the context.
- `ChronicleSlice`: `Prose` and `Anchors` up to `upto_seq`. Both are already separate
  objects (note 05).
- `Given`: a value the application hands in that did not come from a projection, such
  as NVM's `RenderContract`, which is the Thinker's pinned output. It carries the id
  of the event it came from.

A role declares the slices it takes, and gets nothing else. The Narrator's grant is
`Given(contract)` plus `Given(continuity)`, so it holds no `BeliefSlice` to misuse.
This is R24 at runtime: it is the same rule NVM's import-linter enforces at build
time, applied one level down, where a role's code is allowed to import the belief
types for other reasons.

**Every slice records what it handed out.** That is the point of reading through
slices rather than through stores. A context then knows its sources without relying
on the assembler to list them, which makes the demo's §1 defect a test: *no
`SideRemarkOverheard` reaches the speaker's context*. The test asserts on sources,
not on wording, so it still holds when the rendering changes.

### 3b. Parts

```python
class Register(StrEnum):
    binding = "binding"        # must hold; never shed
    considered = "considered"  # advisory, ranked, shed by rank
    craft = "craft"            # load on relevance
    generated = "generated"    # model-written prose: advisory, labelled as such

@dataclass(frozen=True)
class Part:
    slot: str                  # the application's name: "binding", "context", ...
    register: Register
    content: JsonValue         # structured; rendering is the application's
    rank: int = 0              # within a register; higher survives longer
    sources: tuple[Source, ...] = ()   # EventRef | StateRef | SectionRef | GivenRef
```

The registers are NVM's three plus R61's `generated`, which the library adds:

- `binding` maps to INVARIANTS, `considered` to CONSIDERATIONS, and `craft` to CRAFT.
- `generated` is assigned automatically to any part whose sources include chronicle
  prose. An assembler cannot drop the label by mistake, because the slice it read from
  decides the register.

`content` is structured, never pre-rendered. NVM's position is "structured at source,
legible at surface", and its re-serialization A/B (NVM 15:155–173) pins everything
except the serialization. A rendered string would make that experiment impossible.

### 3c. The context, and the budget

```python
@dataclass(frozen=True)
class Context:
    role: str
    upto_seq: int
    parts: tuple[Part, ...]     # what survived, in assembly order
    shed: tuple[Part, ...]      # what the budget dropped, and so what was not shown
```

`assemble(role, assembler, grant, *, budget=None) -> Context`. A `Budget` is a port:
`count(part) -> int`, a total, and an order to shed in. The default sheds
`considered` by rank, then `generated`, then `craft`, and it never sheds `binding`:
"the binding layer never sheds" (U42). If the binding parts alone exceed the total,
assembly raises rather than send a call that violates its own invariants. That
matches the fold rule in note 03 §9: a loud refusal beats a silent omission.

`shed` exists because U42 wants per-slot accounting from M1. "What was the model not
shown" is the question a bad turn raises first.

### 3d. Rendering, and the pin

A renderer is the application's function `Context -> list[ModelMessage]`. The library
ships a plain default that labels each register, enough for the demo and for tests.
The messages then go through `PinnedModel` exactly as they do today. Pin keys are
content-addressed (note 04 §3, note 05 on R61 clause 5), so any assembly change is a
miss and R56 holds: "any prompt-assembly change breaks the keys". **Nothing about
pinning changes in M9.**

## 4. Determinism is the contract — [ours]

An assembler must be a pure function of its grant. Grants read at a position, and
every slice is a fold or a pin, so **the same log and the same position give the same
context, part for part**. The rendered bytes are then the same too, which is what
makes a replay hit its pins. NVM asks for the same thing in two places: "no clock/UUID
in the prefix" (NVM 22:342), and a byte-stable prompt prefix (NVM 29:975).

Two consequences:

- **The golden test is a library helper.** Given a fixture log, a role, an assembler
  and a position, it compares the context against a committed expectation, on parts
  and sources rather than rendered text. That is NVM's WP6 exit criterion, and the
  demo's committed recordings already give it a fixture log.
- **A generated part is only as deterministic as its pin.** Chronicle prose is
  regenerated on a cold rebuild (note 05), and a context built over it changes with
  it. That is correct, because a different chronicle *is* a different prompt. The
  context's sources name which sections it used, so the change is traceable.

## 5. Is the context recorded? — [ours, recommendation]

Today it is not. `PinPromoted` stores the key, which is a digest, and the result. It
does not store the request (`timeline/pins.py`). NVM is inconsistent here:

- 29:529 records a "request digest".
- 21:457 and 22:51 say requests are "pinned as events".
- 22:373 calls serializations "craft debt, not stored artifacts".
- 22:54 relies on "the log therefore *is* a corpus of real `TurnBrief`s".

**Recommendation: don't log the context; make it reassemblable.** A context is
derived from the log, and note 05 §7's rule is that a projection's output does not
belong in the log it projects from. Because assembly is deterministic, the context
any call was shown can be rebuilt with `assemble(..., grant at upto_seq)`.

That covers the "corpus of real briefs" use as long as the assembler code is the
version that ran. That condition is exactly the one the re-serialization A/B keeps
fixed, since it varies only the renderer.

The cost is that once the structure code changes, an old call's exact context can no
longer be rebuilt. It is open (§7) whether NVM wants a record-only manifest for that
case.

## 6. What this would change elsewhere

- **The demo** is ported as the proving consumer. Each model call (propose, update and
  the round summary) gets its own assembler and grant. **The acceptance bar is
  byte-identical prompts, so the committed recordings replay without being
  re-recorded.** The `SideRemarkOverheard` regression becomes a source assertion.
- **R58.** The registry's `model_for(key)` lookup, noted in the PR #19 thread, becomes
  needed: `LogSlice.admit` reads `event_class` off the application's model without
  decoding the payload. It is additive.
- **Note 05.** `Brief` (tail plus depth) stays as it is and becomes the slices'
  implementation underneath. Its tail-from-the-log guarantee (R61 clause 4) carries
  over into `LogSlice` unchanged.

## 7. Open questions

- **A record-only context manifest?** It would be one `engine`-class event per call,
  carrying part slots, registers, sources and a per-part digest, but no text. It would
  let an old call's exact context be checked after the assembler changed, and NVM's
  record-only screens could cite it. The case against is §5. Not built unless you want
  it.
- **Message history as a projection.** Out of scope while no consumer accumulates
  turns. The likely shape is a fold over pinned calls, if one ever does.
- **Sizing the continuity window** stays empirical and the application's (NVM 08
  §9.4, U50). The library offers nothing beyond `Given`.
- **Who populates the continuity window** is unstated in NVM: it is a field of the
  Thinker-authored `RenderContract` (08:70), but 21:498 has it reach the Narrator
  separately. That needs settling in NVM, not here. The grant shape works either way.
- **The judged-validate call's input** conflicts in NVM. Doc 22 says a judge "sees
  only a contract", while R49 queries commitments about the entities involved. A
  grant would make whichever wins explicit. Flagged for NVM.
