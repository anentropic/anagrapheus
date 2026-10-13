# Persona Report

**Generated:** 2026-10-13
**Audience:** Python developers building long-running, replayable LLM agents (advanced)
**Scenarios tested:** 5
**Results:** 3 PASS, 2 PARTIAL, 0 FAIL

## Summary

For this persona the documentation works well. Every titled code example in the tutorials and how-to guides was extracted and run offline (FunctionModel only, no provider keys), and every one produced the output the page shows. That includes the exact `PinMiss` key prefix in Getting Started and the full negotiation tour output. Each how-to has a "Who decides what" table, the vocabulary is defined at first use, and the troubleshooting sections give the real error messages. The two gaps are at the seams between pages. Building a long-context prompt means joining the chronicle guide and the assembly guide, and the join (`ChronicleSlice(...)` construction, `LogSlice.events(since_seq=...)`) appears only in the reference. Schema evolution promises that "folds always see the current shape", but nothing shows how to write an application's own fold.

Method note: scripts were built only from code on the doc pages, plus signatures and docstrings as the generated reference pages render them. They live in `/tmp/claude-0/-home-claude/a6956881-0812-51e0-b715-5f013d06e67b/scratchpad/persona/`. A scratch copy of the site was built with mkdocs (with a stub `CHANGELOG.md`). Every internal link and anchor from the prose pages into the reference resolves.

---

## Scenario S1: Run the negotiation example offline, verify its replay, and locate each layer

**Verdict:** PASS

### Navigation Path

1. Started at: README.md
   - Found: "A complete worked example ... lives in `examples/negotiation/` ... `uv run python -m examples.negotiation`"; link to the documentation.
   - Followed: Documentation, then Overview, then "Where to go next", then Tutorials, then "Touring the Negotiation Example".
2. Navigated to: tutorials/negotiation-example.md
   - Found: clone/sync setup (says plainly that the example is not in the installed package), Step 1 output, then the `tour.py` script.
   - Ran `uv run python -m examples.negotiation`: the output matched the page line for line, ending with "replayed 4 rounds on a fresh store in strict mode".
   - Ran `tour.py` (from the clone root, as instructed): every section matched the page (56 BeliefRevised, 29 PinPromoted, `Counter({'model_call': 28, 'tool_call': 1})`, per-scope counts, four chronicle sections, the six parts with registers, one re-key `Applied(...)`, `divergence: None`).
   - The "Where each layer lives in the example" table maps every layer to a module. Next steps link to the matching how-tos.
   - Language fits the persona: scope, predicate, register and re-key are each defined where they first appear.

---

## Scenario S2: Record my own agent with a pinned tool and replay it offline in a test

**Verdict:** PASS

### Navigation Path

1. Started at: README.md, "First use"
   - Found: a runnable record-then-replay snippet, the same as the Overview's "A first taste".
   - Followed: Getting Started tutorial.
2. Navigated to: tutorials/getting-started.md
   - Ran `summit.py`/`record.py`/`replay.py`/`diverge.py` in a fresh directory. Output matched exactly, including `PinMiss ... 'p1:model_call:6971ba00…' ... after 1 hit(s)` after the documented instruction change.
   - Followed: Next steps, then "Record a run and replay it offline".
3. Navigated to: how-to/record-and-replay.md
   - The script ran and printed the documented four lines (`model calls during replay: 0`).
   - Also checked: the record-mode `announce` snippet (the log ends in `ReplayDiverged`), the string model `PinnedModel("anthropic:claude-haiku-5-5", ...)` with a placeholder key (constructs offline, as the page says), and the claim that `anagrapheus.timeline.register_builtin_events` covers only pins (true).
   - Followed: Related, then "How to Pin Model and Tool Calls".
4. Navigated to: how-to/pin-model-and-tool-calls.md
   - `pin_calls.py`, `pin_policy.py` (custom `CallPolicy` routed through a "gateway" that raises if reached), `pin_retry.py` (`draws: 1 | same key: True`, then `PinsAbandoned`, then draw 2) and `pin_stream.py` all matched their comments.
   - Also checked: the "Resume a unit after a crash" snippet (reopening with `unit_id=` reused the scratch pin: 0 new draws), and the INFO logging (`drawn` / `reused from pin` lines, as described).
   - The content tabs for default and custom policy, and the collapsible note on what a replayed stream does not reproduce, fit this persona's needs.

Minor, not verdict-affecting: the troubleshooting heading `UnknownEventType: no versions registered for ('anagrapheus', 'established')` is what a reader sees. If the registry lacks identity events, `establish(...)` raises earlier, with `UnknownEventType: model not registered: Established`. Listing both messages would help readers match the error they get.

---

## Scenario S3: Private beliefs per agent, then merge two names and re-key in every scope

**Verdict:** PASS

### Navigation Path

1. Started at: README.md, then Overview, then How-To Guides, then "How to Keep Per-Agent Beliefs as a Fold".
2. Navigated to: how-to/per-agent-beliefs.md
   - The five-part `beliefs.py` ran: `{'anagrapheus.beliefs': 3}`, the two held beliefs with stances, one `Disagreement`, and the `BeliefRow` list.
   - The "Verify it worked" rebuild ran, and the same disagreement came back. `beliefs.check(...)` ran. All four troubleshooting errors (`WrongArity`, `UnknownPredicate`, `OutsideVocabulary`, relational `TypeError`) reproduce with the messages shown.
   - Followed the warning about reopening an in-memory store: wrote to a file in one process and reopened it in another. The position was 3 and `divergence` was silently `[]` until the documented rebuild, which restored it. The warning is accurate and necessary.
   - Followed: Related, then "How to Resolve Identity and Re-key Beliefs After a Merge".
3. Navigated to: how-to/identity-and-rekey.md
   - The six-part `identity.py` ran: `True True`, the `RekeyRow(... kind='merge' ...)`, the plan line, both scopes re-keyed, and `['established', 'merged']`.
   - The pinned-mint tip snippet ran verbatim: under `ReplayPins`, `establish` returned the same id with no divergence.
   - The `MergeCycle` prevention check (`registry.canonical(a) != registry.canonical(b)`) correctly reports a reversed merge as unsafe.
   - The mermaid flow and the "Who decides what" table make ownership of the apply loop clear.

Minor friction, not verdict-affecting:
- "After a split, choose the successor" has no code. To try it, `split(types, original, [(new_id, ["name"])])` and `mint_entity_id(...)` had to be found on the reference and Identity explanation pages. The how-to never says where a split product's new id comes from. A short example would close this.
- Only `MemoryCore(InMemoryBackend())` is shown. For a long-running agent, the reader is not told whether a persistent doxastica backend exists, which would avoid a full rebuild on every open. The Beliefs explanation hints at "external" stores ("only you know what kind of store it is") without naming one.

---

## Scenario S4: A long-context prompt with chronicle past and recent offers, granted, budgeted and pinned

**Verdict:** PARTIAL

### Navigation Path

1. Started at: README.md, then How-To Guides, "Long contexts".
2. Navigated to: how-to/chronicle-sections.md
   - `chronicle.py` ran with the documented output (`consolidated through 3`, section `1-3` with its pin, `recent: 4 ...`, `['1-3']`).
   - The `GenerativeRebuild` message matches. The replay-pins rebuild returned identical prose with `divergence None`. `export`/`load` carried 1 section. The `asyncio.to_thread` worker snippet works. A second round consolidated with the same objects as section `4-7`.
   - This page reads recent events through `Brief.tail(...)`, which is not a slice, so it cannot go into a grant. Followed: "label it as generated", then "How to Assemble Prompts".
3. Navigated to: how-to/assemble-prompts.md
   - `assemble_prompts.py` ran with the documented output. The `total=80` claim holds (`shed: ['offer']`), and the leak-check assertion passes.
   - `StaleRead`, `NotGranted`, the grant-reuse `AssemblyError` and `BudgetExceeded` all reproduce with the documented messages. The step 5 `ledger.commit()` really does cause the `StaleRead` the troubleshooting section warns about.
   - **Gap:** "Put chronicle prose in a prompt" shows a `HistoryGrant` dataclass but never shows how to construct a `ChronicleSlice`. The page also never says how to get only the recent events, the ones after the consolidated past, through a `LogSlice`. Both answers are only on the reference page: `ChronicleSlice(store, *, upto_seq)` and `LogSlice.events(*, since_seq=0)`. With those, the combined EU grant (chronicle past + offers after `anchor.to_seq` + a binding ask) assembled correctly and rendered the `SUMMARISED (advisory prose, not canon):` block.

### Gap Analysis

**Where:** how-to/assemble-prompts.md > "Put chronicle prose in a prompt" (and how-to/chronicle-sections.md > "4. Read recent events and the summarised past")
**What:** The page set never shows recency plus depth in one grant, even though that is the reason both layers exist. The chronicle guide reads the recent part with `Brief.tail`, which cannot go into a grant. The assembly guide's chronicle snippet omits `ChronicleSlice` construction, and never mentions `LogSlice.events(since_seq=...)`. The page type fits (how-to), but the steps that join the two how-tos are missing.
**Impact:** An advanced reader gets there through the reference, but has to work out the slice constructors and the since-seq split alone. The second question is how to avoid duplicating events the chronicle already covers.
**Suggested Fix:** In how-to/assemble-prompts.md, section "Put chronicle prose in a prompt": extend the example to a grant with `past=ChronicleSlice(chronicle, upto_seq=upto)` and `room=LogSlice(reader, upto_seq=upto, admit=...)`. The assembler then reads `grant.room.events(since_seq=<last section's anchor.to_seq>)`, so recent offers are verbatim and older ones arrive as `generated` sections. Also show the rendered output. In how-to/chronicle-sections.md, step 4, add one sentence noting that `Brief` is for inspection, and that a prompt should read the same split through these two slices, with a link to the assembly section.

---

## Scenario S5: Evolve an event schema while old logs still read, fold and replay

**Verdict:** PARTIAL

### Navigation Path

1. Started at: README.md, then How-To Guides, then "How to Evolve Event Schemas with Upcasters".
2. Navigated to: how-to/evolve-event-schemas.md
   - `upcast.py` ran with exactly the documented three lines (the stored version stays 1, the reader returns version 2).
   - `UpcastGap`, `DuplicateEventType` (for both a model and an upcast step) and the reserved-namespace `ValueError` reproduce as documented. `reader.read_raw()` returns upcast dicts. `ReplayPins.from_events(..., upcasts=...)` is accepted.
   - **Gap 1:** Step 3 says "give the same reader to every Projector, so folds always see the current shape", and "Verify it worked" asks for "a projector over a fresh projection catches up". No page shows how to write an application fold. The Event Log explanation lists the protocol (`name`, `apply(event, txn)`, `fold`), but not what `event` is or whether its payload is already upcast. The answer that `apply` receives a `StoredEvent` came from the `ProjectionFold` reference page. The fold had to be written by trial; it did see upcast v2 payloads (`{'mackerel': 2}`).
   - **Gap 2 (clarity):** The "Recordings" bullet in step 3 suggests that replaying a recording needs your upcasts after a change like this one. A recording holds only library pin events (`pin_events`), though, so an `OfferMade` upcast never applies to one. The Pinning explanation says the relevant case is a change to the pin event's schema. In this how-to's context, the bullet leaves the reader unsure whether their recordings are at risk.

### Gap Analysis

**Where:** how-to/evolve-event-schemas.md > "3. Read through an EventReader everywhere" and "Verify it worked"; explanation/event-log-and-projections.md > "Folds and projections"
**What:** The how-to relies on an application projection but shows none. The explanation describes the `ProjectionFold` protocol without saying what `apply` receives. The persona needed how-to steps and found only explanation text plus a reference page.
**Impact:** The schema change cannot be verified the way the page says to verify it without stepping outside the guide. The recordings bullet also adds doubt about whether committed fixtures still replay.
**Suggested Fix:** In how-to/evolve-event-schemas.md, section 3: add a short application fold, e.g. a class with `name = "offers_by_stock"`, `fold = "deterministic"` and `apply(self, event: StoredEvent, txn)` reading `event.payload["stock"]`. Run it with `Projector(store, reader, [fold]).catch_up()` and show that the v1 event arrives with `stock`. State that `apply` receives the stored event already upcast. In the same section, reword the Recordings bullet: recordings hold only library pin events, so your own upcasts do not affect them, and `upcasts=` matters when the pin event schema itself changes. In explanation/event-log-and-projections.md, "Folds and projections": say that `apply` receives an upcast `StoredEvent`, and link to the new example.

---

## Cross-cutting observations (not verdict-affecting)

- **Rule 5 in the reference:** the generated reference pages render docstrings that cite internal design IDs. For example, `LogSlice` says "R58's 'every projection declares which classes it folds'" and `Budget` says "(U42)". This persona has never been given those IDs. The prose pages avoid them, as intended.
- **Reference nav quirk:** the module `anagrapheus.beliefs.index` is generated as `reference/anagrapheus/beliefs/index.md`. With section-index enabled, it becomes the landing page of the "beliefs" section, so clicking "beliefs" in the Reference sidebar opens the `BeliefIndex` module rather than an overview. All links still resolve.

---

## Revision Recommendations

### FAIL Issues (trigger revision)

None.

### PARTIAL Issues (for project author approval)

| Scenario | Page | Gap | Suggested Fix |
|----------|------|-----|---------------|
| S4 | how-to/assemble-prompts.md > Put chronicle prose in a prompt | No `ChronicleSlice(...)` construction shown. No example of recent events (`LogSlice.events(since_seq=...)`) and chronicle past in one grant. Both answers are only in the reference. | In how-to/assemble-prompts.md, section "Put chronicle prose in a prompt": show a grant with `ChronicleSlice(chronicle, upto_seq=upto)` plus a `LogSlice` read from the last section's `anchor.to_seq`, with rendered output. |
| S4 | how-to/chronicle-sections.md > 4. Read recent events and the summarised past | `Brief.tail` is shown for recent events, but it cannot be put in a grant. | In how-to/chronicle-sections.md, section 4: say that a prompt reads the same split through `ChronicleSlice` + `LogSlice`, and link to the assembly section. |
| S5 | how-to/evolve-event-schemas.md > 3. Read through an EventReader everywhere / Verify it worked | Relies on an application fold, but none is shown anywhere. What `apply` receives (an upcast `StoredEvent`) appears only in the reference. | In how-to/evolve-event-schemas.md, section 3: add a minimal `ProjectionFold` over `OfferMade`, run through `Projector`, and state that `apply` gets upcast payloads. |
| S5 | how-to/evolve-event-schemas.md > 3 (Recordings bullet) | Implies your own upcasts matter for recordings, but recordings hold only pin events. | In how-to/evolve-event-schemas.md, section 3: reword the bullet so `upcasts=` is tied to changes in the pin event schema, not your own event types. |
| S5 | explanation/event-log-and-projections.md > Folds and projections | Protocol listed without the type of `event` or upcast behaviour. | In explanation/event-log-and-projections.md, "Folds and projections": add one sentence on `apply(event: StoredEvent, txn)` receiving upcast payloads, and link to the how-to example. |
