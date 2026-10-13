# Page Inventory — anagrapheus 0.1.0

**Generated:** 2026-10-13 (doc-author, plan mode)
**Persona:** Python developers building long-running, replayable LLM agents (advanced) — every page targets this single persona.
**Docs root:** `docs/src/` · **Navigation:** sections as top tabs (Overview, Tutorials, How-To Guides, Explanation, Reference, Changelog)

## Proposed Documentation

| # | Type | Title | Key Sections | File Path |
|---|------|-------|--------------|-----------|
| 1 | (index) | Overview | What anagrapheus is (one log, everything else a fold; every draw pinned), who it is for, install (`pip` / `uv add` tabs, `anthropic` / `openai` extras), a 15-line record-then-replay taste, map of the six subpackages, where to go next per tab. Replaces the current stub. | docs/src/index.md |
| 2 | (index) | Tutorials | Intro; bold link + 1-sentence abstract for pages 3–4; which to start with | docs/src/tutorials/index.md |
| 3 | tutorial | Getting Started: Record and Replay an Agent | Prerequisites; open a `SQLiteEventStore` and register events (`anagrapheus.register_builtin_events`); wrap a pydantic-ai `FunctionModel` in `PinnedModel`; run a short two-agent exchange; write a recording; replay it on a fresh store with `ReplayPins` and see that no model is reached; change the prompt and meet `PinMiss`/`Divergence`; swap in a real provider (tab); what you learned. Self-contained — needs only `pip install anagrapheus`. | docs/src/tutorials/getting-started.md |
| 4 | tutorial | Touring the Negotiation Example | Clone the repository; run `python -m examples.negotiation` offline; read the output layer by layer (log, pins, beliefs per party, identity merge and re-key, chronicle, assembled prompts); replay and its cost line; optional `--model` run against a real provider; optional browser UI (`python -m examples.negotiation.ui`); where each layer's code lives in the example. | docs/src/tutorials/negotiation-example.md |
| 5 | (index) | How-To Guides | Intro; bold link + 1-sentence abstract for pages 6–12 | docs/src/how-to/index.md |
| 6 | how-to | How to Record a Run and Replay It Offline | Prerequisites; register every library event in one call; append domain events; `write_recording` / `read_recording`; replay with `ReplayPins.from_events` on a fresh store; verify zero model calls; troubleshooting (under-registration → `UnknownEventType`, `RecordingError`, `PinMiss` after a code change) | docs/src/how-to/record-and-replay.md |
| 7 | how-to | How to Pin Model and Tool Calls | `PinnedModel` around any pydantic-ai model; `pinned_tool`; default `SemanticCallPolicy` vs a custom `CallPolicy` (tabs) — the application decides what "the same call" means; retries reuse the draw within a unit of work; streaming via `PinnedStreamedResponse` and what replay does not reproduce (collapsible); troubleshooting (fresh key every call, `PinsAbandoned`) | docs/src/how-to/pin-model-and-tool-calls.md |
| 8 | how-to | How to Keep Per-Agent Beliefs as a Fold | Declare predicates in a `PredicateRegistry` (arity, value vocabulary); bind domain events to an `Interpreter` or emit `BeliefRevised`/`BeliefExpanded`/`BeliefContracted` via `BeliefEvents`; run `BeliefFold` through a `Projector`; read per scope; measure disagreement with `divergence`; common mistakes (`WrongArity`, `UnknownPredicate`, `OutsideVocabulary`) | docs/src/how-to/per-agent-beliefs.md |
| 9 | how-to | How to Resolve Identity and Re-key Beliefs After a Merge | `establish` vs `alias`/`merge`/`split` decisions as events; `IdentityFold` and `IdentityRegistry`; `rekeys_since`; `plan_rekeys` over the `BeliefIndex`; the application applies the plan (example from `examples/negotiation/rekeying.py`); mermaid: merge → plan → application applies; troubleshooting (`MergeCycle`, `AmbiguousSurfaceForm`, `UnknownEntity`) | docs/src/how-to/identity-and-rekey.md |
| 10 | how-to | How to Consolidate History into Chronicle Sections | Supply a `Summarizer` and unit boundaries to `ChronicleFold`; read `Recent` plus `Brief`; `Anchors` (binding) vs `Prose` (generated); `Retriever` / `substring_retriever`; carry a chronicle with `SQLiteChronicleStore` export/load; warning: rebuilding a generative fold re-runs every model call (`GenerativeRebuild`) | docs/src/how-to/chronicle-sections.md |
| 11 | how-to | How to Assemble Prompts from Granted Slices | Define a grant (`EventsSlice`, `LogSlice`, `BeliefSlice`, `ChronicleSlice`, `Given`); build `Part`s with a `Register`, rank and sources; `assemble` with a `Budget` (never sheds a binding part); `render_labelled`; send through `PinnedModel`; audit with `handed_out`; troubleshooting (`NotGranted`, `UncitedSource`, `MislabelledPart`, `BudgetExceeded`, `StaleRead`) | docs/src/how-to/assemble-prompts.md |
| 12 | how-to | How to Evolve Event Schemas with Upcasters | Bump a `DomainEvent` schema version; register an `Upcast` in an `UpcastRegistry`; read through `EventReader`; verify an old log still folds; troubleshooting (`UpcastGap`, `SchemaTooNew`, `DuplicateEventType`). Self-contained example — the negotiation example has no upcasters. | docs/src/how-to/evolve-event-schemas.md |
| 13 | (index) | Explanation | Intro; bold link + 1-sentence abstract for pages 14–20; suggested reading order | docs/src/explanation/index.md |
| 14 | explanation | How the Layers Fit Together | One authoritative log, every other component a projection; mermaid of log → folds; how a turn flows (assemble → `PinnedModel` → events → folds); what is the library's job vs the application's (call policy, predicate vocabularies, scope lists, identity judgments, applying re-key plans, rendering, budgets, summariser prompts); when not to use it | docs/src/explanation/architecture.md |
| 15 | explanation | The Event Log and Projections | Envelope, position, fold, projection; deterministic vs generative folds (`FoldDeterminism`); `Projector` catch-up and rebuild; why projections are disposable; reserved library namespace; upcasting as read-time schema evolution | docs/src/explanation/event-log-and-projections.md |
| 16 | explanation | Pinning and Replay | What pinning means; keys derived from what was asked (`pin_key`, call policy); replay answers from the log vs re-running calls the model; retry reuse, promotion and abandonment; pin misses and divergence as a feature; comparison with HTTP cassette recording and response caching; mermaid record/replay sequence | docs/src/explanation/pinning-and-replay.md |
| 17 | explanation | Beliefs | Scope, proposition, predicate; revise / expand / contract; doxastica holds beliefs, anagrapheus supplies ids and the fold; why the id names the question and arity is declared; the entity → belief index; divergence between holders | docs/src/explanation/beliefs.md |
| 18 | explanation | Identity | Establish vs resolve; decisions as events and the registry as a fold; merge and split, superseding a bad merge; re-keying as a belief operation the application owns; the `Ladder` and `IdentityVocabulary` | docs/src/explanation/identity.md |
| 19 | explanation | The Chronicle | Non-destructive compaction; sections that cite their range; supersede rather than correct; recency vs depth; anchors (replayable) vs prose (regenerated); why rebuild needs acknowledgement; why the chronicle is carried rather than rebuilt across a backend swap | docs/src/explanation/chronicle.md |
| 20 | explanation | Prompt Assembly | Grant, slice, part, register, budget; why a role can read only what it was granted; citations and provenance; why assembly changes become pin misses; what is left to the application (rendering, slot names, budgets) | docs/src/explanation/prompt-assembly.md |
| 21 | (index) | Reference | Short intro to the generated API reference; one bold link + 1-sentence abstract per subpackage (`timeline`, `provider`, `identity`, `beliefs`, `chronicle`, `assembly`) pointing into the mkdocstrings pages; note that symbols are documented at their defining module | docs/src/reference/index.md |
| 22 | (readme) | README.md | Title, badges, accurate description (current README claims nothing is implemented), install, minimal record/replay example, key features, docs link, license | README.md |
| 23 | (maintainer) | MAINTAINER.md | Dev setup, common tasks, architecture, CI, release, decision log. **Recommend excluding:** `DEVELOP.md` and `AGENTS.md` already cover this material. | MAINTAINER.md |

Pages 1–21 are the 0.1.0 set. 22 is strongly recommended (the README is stale and is the PyPI landing page). 23 is offered per the plugin's convention but likely redundant.

## API Reference Status

**Detected:** mkdocstrings, generated by `docs/scripts/gen_ref_pages.py` (mkdocs-gen-files + literate-nav) into `reference/`, one page per public module (`reference/anagrapheus/<subpackage>/<module>.md`); `__init__` and `_`-prefixed modules are skipped. No per-symbol reference pages are proposed. Narrative pages will link inline code mentions to the defining module's page (e.g. `PinnedModel` → `anagrapheus.provider.model`), not the re-exporting package.

Page 21 is a hand-written landing page. It must sit at `reference/index.md` alongside the generated pages, so the `nav:` entry needs to combine it with the literate-nav `SUMMARY.md` (check that gen-files does not overwrite it).

## Audience Targeting

One persona is configured, and every page targets it. Calibration notes:

- Tutorials 3 and 4 run offline first (`FunctionModel` / scripted), then show the real-provider variant in a tab.
- Each how-to and explanation page defines the never-assume terms it uses (envelope, upcast, fold, projection, position, pin, scope, predicate, revise/expand/contract, establish/merge/re-key, section, grant/slice/part/register/budget) on first use and links them to the Explanation page that owns each term.
- Page 14 is where the persona's "which pieces are the library's job and which are the application's" question gets a full answer. Each how-to also restates it locally (project rule).

## Coverage Gaps and Notes for the Orchestrator

- **Not covered by a dedicated page (mentioned within others):** `IdentityVocabulary`/`Ladder` details, `ScratchRow`/`PinScratchStore`/`TransactionalStore` and the other store protocols, `migrations` modules, writing a custom backend instead of SQLite. Custom store backends are out of scope for 0.1.0; the reference covers the protocols.
- **Gap-report symbols** (171) all get narrative coverage through pages 6–20 at the subpackage level. Low-level row/record types (`AliasRow`, `DecisionRow`, `EdgeRecord`, `BeliefRow`, …) are left to the reference.
- **Logging:** the library logs under the `anagrapheus` logger and never configures it. This gets one short tip in page 6 or 14, not a page of its own.
- **Use Cases section:** `use_cases_section: false`. No section is proposed, but both use cases frame pages 1, 3, 14 and 16.
- **Required config changes outside `.doc-writer/` (not made, as instructed):**
  - `mkdocs.yml` needs `navigation.tabs`, `navigation.tabs.sticky` and `navigation.indexes`.
  - `nav:` needs rewriting: "Home" → "Overview", plus Tutorials / How-To Guides / Explanation / Reference tabs.
  - `docs/src/changelog.md` snippets-includes `CHANGELOG.md`, which does not exist. With `check_paths: true` the build fails until that file is created or the include is removed.
- **Tutorial 4** requires a clone of the repository: `examples/negotiation/` is not part of the installed package. Its docstrings cite test files, milestones and design-note numbers, and these must not be copied into the pages.
- **Docstrings** in `provider`, `chronicle`, `assembly` and `identity` cite "doc 00 §…", "note 07" and narrative-vm R-numbers. These will show verbatim in the generated reference. That is not fixable from the docs pages; consider a docstring cleanup before release.
