# Editor Report

**Generated:** 2026-10-13
**Files reviewed:** 22 (21 pages in `docs/src/` plus `README.md`; `changelog.md` is a snippet include)
**Changes made:** 124
  - BLOCKING: 80
  - SUGGESTION: 22
  - NITPICK: 22

## Summary

The three author groups wrote to one consistent vocabulary and a shared structural template (every how-to has Before you start, Who decides what, numbered steps, Verify it worked, Troubleshooting and Related; every tutorial ends with What you've learned and Next steps), so editing was light-touch. Most fixes are cross-reference links to the generated API reference (first unlinked mention of each public symbol per page, plus every symbol on the reference landing page), one accuracy fix on merge cycles, a handful of term normalisations (entity index, scratch area, stance tokens, names), and a few AI-pattern removals. All internal-note blocks were removed. `mkdocs build --strict` passes, and a crawl of the built site found no broken internal links or heading anchors.

**Not changed, by design:** `README.md` (no notes block; its Development section is maintainer-facing but belongs in a README; its inline code mentions are left unlinked because the README also renders on GitHub and PyPI); fenced code (apart from one comment edit, re-run); headings (no explanation-page heading was renamed, so every cross-page anchor link still resolves).

---

## docs/src/explanation/architecture.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `Context` (first mention on the page) | Linked to `anagrapheus.assembly.parts.Context` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| One log, many projections | Term normalised: 'belief index' to 'entity index' | Replaced |
| How one turn flows | Term normalised: 'scratch space' to 'scratch area' (used by both pinning how-tos) | Replaced |
| What the library decides | Filler adverb 'consistently ships the mechanism' | Removed 'consistently' |
| Why the log has to be the authority | Vague 'it is handled with care' | Now 'with its own rules for rebuilding' |

---

## docs/src/explanation/beliefs.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `WrongArity` (first mention on the page) | Linked to `anagrapheus.beliefs.errors.WrongArity` |
| Prose | Unlinked API mention `OutsideVocabulary` (first mention on the page) | Linked to `anagrapheus.beliefs.errors.OutsideVocabulary` |
| Prose | Unlinked API mention `BeliefRevised` (first mention on the page) | Linked to `anagrapheus.beliefs.events.BeliefRevised` |
| Prose | Unlinked API mention `BeliefExpanded` (first mention on the page) | Linked to `anagrapheus.beliefs.events.BeliefExpanded` |
| Prose | Unlinked API mention `BeliefContracted` (first mention on the page) | Linked to `anagrapheus.beliefs.events.BeliefContracted` |
| Prose | Unlinked API mention `PendingEvent` (first mention on the page) | Linked to `anagrapheus.timeline.envelope.PendingEvent` |
| Prose | Unlinked API mention `BeliefEvents.check(*ops)` (first mention on the page) | Linked to `anagrapheus.beliefs.writer.BeliefEvents.check` |
| Prose | Unlinked API mention `UnresolvedStateRef` (first mention on the page) | Linked to `anagrapheus.beliefs.errors.UnresolvedStateRef` |
| Prose | Unlinked API mention `BeliefFold.store` (first mention on the page) | Linked to `anagrapheus.beliefs.fold.BeliefFold.store` |

---

## docs/src/explanation/chronicle.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `consolidated_through()` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Anchors.consolidated_through` |
| Prose | Unlinked API mention `tail(since_seq=...)` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Brief.tail` |
| Prose | Unlinked API mention `depth()` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Brief.depth` |
| Prose | Unlinked API mention `Projector.rebuild` (first mention on the page) | Linked to `anagrapheus.timeline.folds.Projector.rebuild` |
| Prose | Unlinked API mention `GenerativeRebuild` (first mention on the page) | Linked to `anagrapheus.timeline.folds.GenerativeRebuild` |
| Prose | Unlinked API mention `export()` (first mention on the page) | Linked to `anagrapheus.chronicle.store.ChronicleStore.export` |
| Prose | Unlinked API mention `load(sections)` (first mention on the page) | Linked to `anagrapheus.chronicle.store.ChronicleStore.load` |
| Prose | Unlinked API mention `substring_retriever` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.substring_retriever` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| The summariser builds the key | Personifying 'would happily serve' | 'would still serve' |

---

## docs/src/explanation/event-log-and-projections.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `UnknownEventType` (first mention on the page) | Linked to `anagrapheus.timeline.errors.UnknownEventType` |
| Prose | Unlinked API mention `Projector.catch_up()` (first mention on the page) | Linked to `anagrapheus.timeline.folds.Projector.catch_up` |
| Prose | Unlinked API mention `SQLiteEventStore` (first mention on the page) | Linked to `anagrapheus.timeline.sqlite.SQLiteEventStore` |
| Prose | Unlinked API mention `Projector.rebuild(name, reset=...)` (first mention on the page) | Linked to `anagrapheus.timeline.folds.Projector.rebuild` |
| Prose | Unlinked API mention `GenerativeRebuild` (first mention on the page) | Linked to `anagrapheus.timeline.folds.GenerativeRebuild` |
| Prose | Unlinked API mention `UpcastGap` (first mention on the page) | Linked to `anagrapheus.timeline.errors.UpcastGap` |
| Prose | Unlinked API mention `SchemaTooNew` (first mention on the page) | Linked to `anagrapheus.timeline.migrations.SchemaTooNew` |

---

## docs/src/explanation/identity.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Merge and split | Accuracy mismatch: page said a merge cycle is remedied by 'another decision that breaks the cycle'. Source (`identity/sqlite.py merged()`, `IdentityRegistry.canonical`) never clears `merged_into`, and no decision withdraws a merge. | Rewrote: no decision in this version withdraws a merge; guard before appending by checking the canonical ids differ (matches the how-to's troubleshooting). |
| Prose | Unlinked API mention `Established` (first mention on the page) | Linked to `anagrapheus.identity.events.Established` |
| Prose | Unlinked API mention `Aliased` (first mention on the page) | Linked to `anagrapheus.identity.events.Aliased` |
| Prose | Unlinked API mention `Merged` (first mention on the page) | Linked to `anagrapheus.identity.events.Merged` |
| Prose | Unlinked API mention `Split` (first mention on the page) | Linked to `anagrapheus.identity.events.Split` |
| Prose | Unlinked API mention `PendingEvent` (first mention on the page) | Linked to `anagrapheus.timeline.envelope.PendingEvent` |
| Prose | Unlinked API mention `Resolution` (first mention on the page) | Linked to `anagrapheus.identity.registry.Resolution` |
| Prose | Unlinked API mention `Resolution.sole()` (first mention on the page) | Linked to `anagrapheus.identity.registry.Resolution.sole` |
| Prose | Unlinked API mention `AmbiguousSurfaceForm` (first mention on the page) | Linked to `anagrapheus.identity.errors.AmbiguousSurfaceForm` |
| Prose | Unlinked API mention `MergeCycle` (first mention on the page) | Linked to `anagrapheus.identity.errors.MergeCycle` |
| Prose | Unlinked API mention `MergeCycleInBatch` (first mention on the page) | Linked to `anagrapheus.beliefs.errors.MergeCycleInBatch` |
| Prose | Unlinked API mention `UnrenderableBelief` (first mention on the page) | Linked to `anagrapheus.beliefs.errors.UnrenderableBelief` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Decisions are events / Merge and split | Synonym cycling: 'named forms', 'forms' for the names attached to an entity | Normalised to 'names' |
| Why the library stops at the plan | Said the how-to 'walks through' `rekeying.py`; the how-to shows a simplified loop | Now says it walks through a simplified version of the same loop |
| End of page | Only layer explanation without the closing 'For the procedure' link (Rule 3) | Added link to the identity how-to |

---

## docs/src/explanation/pinning-and-replay.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `PinsAbandoned` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinsAbandoned` |
| Prose | Unlinked API mention `ReplayPins` (first mention on the page) | Linked to `anagrapheus.timeline.replay.ReplayPins` |
| Prose | Unlinked API mention `ReplayDiverged` (first mention on the page) | Linked to `anagrapheus.timeline.pins.ReplayDiverged` |
| Prose | Unlinked API mention `resolve(key, kind=..., effect=..., interpret=...)` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinResolver.resolve` |
| Prose | Unlinked API mention `establish` (first mention on the page) | Linked to `anagrapheus.identity.decisions.establish` |
| Prose | Unlinked API mention `StoredEvent` (first mention on the page) | Linked to `anagrapheus.timeline.envelope.StoredEvent` |
| Prose | Unlinked API mention `pin_events(...)` (first mention on the page) | Linked to `anagrapheus.timeline.recording.pin_events` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Replay is not running again | Filler 'It is worth being precise about the difference, because the two are easy to conflate.' | 'The two are easy to conflate, and the difference matters.' |
| What counts as the same call | Inflated 'The key is the whole design' | 'Pinning stands or falls on the key' |

---

## docs/src/explanation/prompt-assembly.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `NotGranted` (first mention on the page) | Linked to `anagrapheus.assembly.errors.NotGranted` |
| Prose | Unlinked API mention `AssemblyError` (first mention on the page) | Linked to `anagrapheus.assembly.errors.AssemblyError` |
| Prose | Unlinked API mention `StaleRead` (first mention on the page) | Linked to `anagrapheus.assembly.errors.StaleRead` |
| Prose | Unlinked API mention `UncitedSource` (first mention on the page) | Linked to `anagrapheus.assembly.errors.UncitedSource` |
| Prose | Unlinked API mention `MislabelledPart` (first mention on the page) | Linked to `anagrapheus.assembly.errors.MislabelledPart` |
| Prose | Unlinked API mention `BudgetExceeded` (first mention on the page) | Linked to `anagrapheus.assembly.errors.BudgetExceeded` |
| Prose | Unlinked API mention `Retriever` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Retriever` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Grants | Internal design vocabulary leaked ('narrator-style role', 'Given contract', 'continuity window'): terms the reader is never given (persona never-assume rule) | Rewrote as 'A role granted only two `Given` values, such as its instructions and a window of recent text' |

---

## docs/src/how-to/assemble-prompts.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `PinnedModel` (first mention on the page) | Linked to `anagrapheus.provider.model.PinnedModel` |
| Prose | Unlinked API mention `PinMiss` (first mention on the page) | Linked to `anagrapheus.timeline.replay.PinMiss` |
| Prose | Unlinked API mention `PinLedger` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinLedger` |
| Prose | Unlinked API mention `PinPromoted` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinPromoted` |

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| 2. Define the role's grant / 3. Write the assembler | Code comments numbered '# 1.' and '# 2.' under steps 2 and 3 | Dropped the numbers (comment-only change; page code re-extracted and re-run, output unchanged) |

---

## docs/src/how-to/chronicle-sections.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `PinnedModel` (first mention on the page) | Linked to `anagrapheus.provider.model.PinnedModel` |
| Prose | Unlinked API mention `consolidated_through()` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Anchors.consolidated_through` |
| Prose | Unlinked API mention `ChronicleSlice` (first mention on the page) | Linked to `anagrapheus.assembly.slices.ChronicleSlice` |
| Prose | Unlinked API mention `Projector.rebuild` (first mention on the page) | Linked to `anagrapheus.timeline.folds.Projector.rebuild` |
| Prose | Unlinked API mention `ReplayPins` (first mention on the page) | Linked to `anagrapheus.timeline.replay.ReplayPins` |
| Prose | Unlinked API mention `Brief.tail` (first mention on the page) | Linked to `anagrapheus.chronicle.reading.Brief.tail` |

---

## docs/src/how-to/evolve-event-schemas.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `upcast(...)` (first mention on the page) | Linked to `anagrapheus.timeline.upcast.UpcastRegistry.upcast` |
| Prose | Unlinked API mention `Projector` (first mention on the page) | Linked to `anagrapheus.timeline.folds.Projector` |
| Prose | Unlinked API mention `read_recording` (first mention on the page) | Linked to `anagrapheus.timeline.recording.read_recording` |
| Prose | Unlinked API mention `ReplayPins.from_events(recording.events, types, upcasts=upcasts)` (first mention on the page) | Linked to `anagrapheus.timeline.replay.ReplayPins.from_events` |

---

## docs/src/how-to/identity-and-rekey.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `Resolution.sole()` (first mention on the page) | Linked to `anagrapheus.identity.registry.Resolution.sole` |
| Prose | Unlinked API mention `Ladder` (first mention on the page) | Linked to `anagrapheus.identity.ladder.Ladder` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| 5. Apply the plan in every scope | Author note: simplified loop does not cover a belief naming two re-keyed entities or split choosers | Added one sentence pointing to `apply_rekeys` in `examples/negotiation/rekeying.py` for the general case (verified it has `choose=` and multi-plan handling) |
| Troubleshooting | Term normalised: 'stance words' to 'stance tokens' (source docstrings in `identity/ladder.py`) | Replaced |

---

## docs/src/how-to/per-agent-beliefs.md

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| 3. Wire the fold | 'entity index' was used on other pages but never introduced in the guides | Introduced **entity index** where the fold's index is first described |
| Verify it worked | Term normalised: 'belief index' to 'entity index' | Replaced |

---

## docs/src/how-to/pin-model-and-tool-calls.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `PinPromoted` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinPromoted` |
| Prose | Unlinked API mention `PinsAbandoned` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinsAbandoned` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Verify it worked | 'makes this easy' filler | 'a `FunctionModel` delegate is the simplest place' |

---

## docs/src/how-to/record-and-replay.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `EventTypeRegistry` (first mention on the page) | Linked to `anagrapheus.timeline.registry.EventTypeRegistry` |
| Prose | Unlinked API mention `PinPromoted` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinPromoted` |
| Prose | Unlinked API mention `ReplayDiverged` (first mention on the page) | Linked to `anagrapheus.timeline.pins.ReplayDiverged` |
| Prose | Unlinked API mention `read_recording` (first mention on the page) | Linked to `anagrapheus.timeline.recording.read_recording` |

---

## docs/src/reference/index.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| All subpackage lists | ~90 symbol names in the module bullets were plain inline code with no link to their entries | Linked each one to its mkdocstrings anchor on the module page that defines it (type aliases `FoldDeterminism`, `Upcast`, `Interpreter`, `Source` left plain: mkdocstrings emits no anchor for them) |
| Chronicle, Assembly, Beliefs, Timeline | Module bullets omitted public symbols the guides use: `UnitBoundary`, `EventTypes`, `EventRef`, `SectionRef`, `GivenRef`, `StateRef`, `Recording`, `RecordingError` | Added them, linked, to the relevant bullets |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Identity | Term normalised: 'stance words' to 'stance tokens' | Replaced |
| Beliefs | Term normalised: 'entity-to-belief index' to 'entity index' | Replaced |

---

## docs/src/tutorials/getting-started.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `PinMiss` (first mention on the page) | Linked to `anagrapheus.timeline.replay.PinMiss` |
| Prose | Unlinked API mention `pinned_tool` (first mention on the page) | Linked to `anagrapheus.provider.tools.pinned_tool` |

---

## docs/src/tutorials/negotiation-example.md

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Prose | Unlinked API mention `BeliefRevised` (first mention on the page) | Linked to `anagrapheus.beliefs.events.BeliefRevised` |
| Prose | Unlinked API mention `PinnedModel` (first mention on the page) | Linked to `anagrapheus.provider.model.PinnedModel` |
| Prose | Unlinked API mention `PinLedger` (first mention on the page) | Linked to `anagrapheus.timeline.pins.PinLedger` |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Step 1 | Sycophantic opener 'Great: a full negotiation ran...' | Removed 'Great:'; 'Here is what each line reports.' |
| Step 3 | Filler 'really reached once' | Removed 'really' |
| Step 7 | Term normalised: 'entity-to-belief BeliefIndex' to 'the entity index, a BeliefIndex from each entity to the belief ids that name it' | Replaced |
| Steps 3-8 (type check) | Tour steps lean toward explanation (why there are 29 pins, why the library stops at the plan) but each paragraph interprets output the reader has just produced | No change; acceptable for a guided tour. Flag if a stricter tutorial voice is wanted |

---

## All pages

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| End of page | `<!-- INTERNAL NOTES FOR EDITOR -->` block present on 21 pages | Removed; extra blank lines collapsed without touching code-block spacing. Content worth keeping is below |
| Prose | Em dashes, curly quotes, chatbot artefacts, knowledge-cutoff disclaimers | None found in prose (em dashes appear only inside verbatim program output, left alone) |

---

## Terminology Changes

| Term | Before | After | Authority |
|------|--------|-------|-----------|
| Entity-to-belief index | "belief index", "entity-to-belief index" | "entity index" | Explanation page "The entity index" (Beliefs), most frequent form; class is `BeliefIndex` |
| PinLedger's uncommitted pins | "scratch space" | "scratch area" | Most frequent form (both pinning how-tos) |
| Ladder rungs | "stance words" | "stance tokens" | `identity/ladder.py` docstrings |
| Names on an entity | "forms", "named forms" | "names" | Prose term used everywhere else; code says `surface_form` |

Already consistent (no change): summariser (prose) vs `Summarizer`/`summarize=` (code); re-key (prose) vs `rekeys_since`/`plan_rekeys` (code); position (never watermark, except the deliberate contrast); British spelling throughout; pydantic-ai, doxastica, SQLite, UUIDv7. Term map written to `.doc-writer/terminology.yaml`.

---

## Carried over from the authors' notes (for the maintainer)

These were in the stripped HTML comments and are worth keeping.

**Library gaps and source issues**

- **No way to withdraw a merge.** Docstrings in `identity/__init__.py`, `IdentityRegistry.canonical` and `identity.errors.MergeCycle` say a bad merge is "undone by superseding it", but no event does this: `Merged` sets `merged_into` permanently, a reverse merge produces `MergeCycle`, and `split()` creates new ids rather than restoring the absorbed one. The docs now say this plainly (identity explanation and how-to). Either add a corrective event or fix the docstrings.
- **Stale docstring:** `PredicateKind` in `src/anagrapheus/beliefs/predicates.py` still refers to `BeliefWriter`, which no longer exists. It shows in the generated reference.
- **Internal citations in the generated reference:** many public docstrings cite design notes ("doc 00 §…", "note 07", R-numbers such as R21/R61, NVM). The prose pages avoid them, but mkdocstrings renders them verbatim.
- **Synchronous summariser:** `ChronicleFold` and its summariser are synchronous. `Agent.run_sync` inside a running event loop raises `RuntimeError`, and `asyncio.to_thread(projector.catch_up)` fails because SQLite connections are thread-bound. The chronicle how-to documents a worker-thread-with-its-own-store pattern (verified), but this is friction worth an async path.
- **No scope enumeration:** neither anagrapheus nor doxastica's `MemoryCore` can list scopes, so the application must keep its own list (re-keying, the negotiation tour).
- **No library helper applies re-key plans:** the loop lives in `examples/negotiation/rekeying.py` (decided for 0.1.0).

**Reference generation**

- The top-level `anagrapheus.register_builtin_events` has no reference page, because `docs/scripts/gen_ref_pages.py` skips every `__init__.py`. It is therefore unlinked on every page that mentions it. A hand-written anchor or a generated `anagrapheus.md` page would fix that.
- Type aliases (`FoldDeterminism`, `Upcast`, `Interpreter`, `Source`) get no mkdocstrings anchor, so they stay as plain code.
- `docs/src/index.md` links subpackage rows to the `## Timeline` … `## Assembly` headings on `reference/index.md` (`#timeline` etc.). Keep those headings stable.

**Unverified or environment-dependent claims**

- Real-provider steps were construction-checked only, never run against a live API (no key): Getting Started Step 5, the negotiation tour Step 9, and the record/replay how-to's provider section. Construction with a dummy `ANTHROPIC_API_KEY` and strict `ReplayPins` raising before any network call were verified.
- The pip/uv install tabs assume `anagrapheus` is published on PyPI (the README carries a PyPI badge; version is 0.1.0). doxastica's PyPI link was confirmed against `uv.lock` (registry source pypi.org).
- The `PinMiss` key hash in Getting Started is elided with an ellipsis because it depends on the pydantic-ai version; UUIDs in the negotiation tour and beliefs outputs vary per run and are shortened.
- The negotiation tour's `tour.py` imports the example's own, non-public modules (`Table`, `proposing`, `apply_rekeys`). That is acceptable for a page about the example, but it will break if the example is refactored.
- pydantic-ai prints a banner to stderr; shown outputs are stdout only (set `PYDANTIC_AI_NO_BANNER=1`).

**Known limitations the pages state**

- A replayed stream does not reproduce chunk boundaries or timing.
- Pins record meaning, not wire bytes, so SDK framing changes are not caught.
- Assembly cannot detect an assembler copying section prose into a non-`generated` part; nothing enforces the "prose never settles anything" rule once prose has been read.
- Two chronicles over one log cannot be merged (range-derived section ids collide).
- Nothing enforces the establish/resolve distinction.
- Re-consolidating a chronicle range by hand has no library API and sits outside the replay guarantee.

**Possible follow-ups (not done)**

- `README.md` key-features bullets could link symbols to absolute docs URLs (`https://anentropic.github.io/anagrapheus/reference/...`) if wanted on GitHub/PyPI.
- The term "unit of work" means both a `PinLedger` commit group and a chronicle consolidation unit, as it does in the source. The pages keep them apart by context, but a reader may conflate them. Consider "unit" or "consolidation unit" on the chronicle pages if this causes confusion.
