# Doc Writer Context

**Generated:** 2026-10-13
**Source:** /doc-writer:setup researcher
**Editable:** Yes -- manual edits are preserved until the next --refresh-context run. To make permanent changes, edit config.yaml and re-run setup.

## Project Summary

anagrapheus (v0.1.0, Python >=3.14, BSD-3-Clause, by Anentropic) is an event-sourced stack for long-running LLM agents: one append-only event log is the authoritative record, and everything else -- per-agent beliefs, entity identity, a consolidated chronicle, assembled prompts -- is a projection (fold) over it that can be dropped and rebuilt. Every nondeterministic effect (model call, tool call, id minting) is pinned to the log by key, so a run replays offline exactly or reports where it diverged. It builds on pydantic-ai (the model seam, provider-agnostic) and doxastica (the per-scope belief store).
Inferred from: pyproject.toml, subpackage `__init__` docstrings and `__all__`, `examples/negotiation/`. The README is stale (claims nothing is implemented) -- ignore its status line.

**Public surface** (import from these; never from `_`-prefixed modules such as `timeline._canonical`, even though `timeline` re-exports `canonical_json`, `sha256_hex`, `Json` from it):
- `anagrapheus` -- the six subpackages plus `register_builtin_events(registry)` (registers timeline, identity and beliefs events in one call; under-registering raises `UnknownEventType` later).
- `anagrapheus.timeline` -- envelope (`DomainEvent`, `PendingEvent`, `StoredEvent`), `EventStore`/`SQLiteEventStore`, `EventTypeRegistry` (reserved library namespace), upcasting (`UpcastRegistry`, `EventReader`), folds (`ProjectionFold`, `GenerativeFold`, `Projector`, `FoldDeterminism`), pinning (`PinLedger`, `pin_key`, `ReplayPins`, `PinMiss`, `Divergence`), recordings (`write_recording`, `read_recording`).
- `anagrapheus.provider` -- `PinnedModel` (wraps any pydantic-ai model; pins per request), `pinned_tool`, `CallPolicy`/`SemanticCallPolicy`, `PinnedStreamedResponse`.
- `anagrapheus.identity` -- `establish`, `alias`, `merge`, `split`, `mint_entity_id`, `IdentityFold`, `IdentityRegistry` (`rekeys_since`), `Ladder`, `IdentityVocabulary`, `SQLiteIdentityStore`.
- `anagrapheus.beliefs` -- `PredicateRegistry`/`Predicate` (the only way to mint belief ids; declared arity), `BeliefFold`, ops (`Revise`, `Expand`, `Contract`, `Interpreter`), `BeliefEvents` writer, `BeliefIndex`, `plan_rekeys`/`BeliefRekey`, `divergence`.
- `anagrapheus.chronicle` -- `ChronicleFold` (a generative fold: its apply calls a model), `Summarizer`, `Section`/`Anchor`, reading via `Anchors` (binding) vs `Prose` (generated), `Brief`, `Recent`, `Retriever`, `SQLiteChronicleStore` (export/load).
- `anagrapheus.assembly` -- slices granted to a role (`EventsSlice`, `LogSlice`, `BeliefSlice`, `ChronicleSlice`, `Given`), `Part`/`Register`/`Source`, `assemble`, `Budget` (never sheds a binding part), `render_labelled`.

Storage is stdlib sqlite3 behind store protocols (one database file can hold every component). The library logs under the `anagrapheus` logger and never configures logging.

## User Persona: Python developers building long-running, replayable LLM agents

### Profile
- **Skill level:** advanced
- **What they know:** Modern Python (3.12+): type hints including generics, dataclasses, `async`/`await` and async context managers -- do not explain these. pydantic models and validation, and pydantic-ai agents, models, tools, `FunctionModel`/`TestModel` and model strings like `anthropic:...` -- name them without introduction. That LLM calls cost money, are slow, and return different output for the same input. SQLite as a file-backed database and the idea of persisting state between runs.
- **What to always explain:**
  - Event-sourcing vocabulary as this library uses it: *envelope* (the metadata wrapper around a domain event), *upcast* (rewriting an old event schema version on read), *fold* (a function that applies events in order to build state), *projection* (state derived from the log, disposable and rebuildable), *position* (the log sequence number a projection has consumed -- the library says "position", not "watermark").
  - Pinning: recording a nondeterministic draw under a key derived from what was asked, so a retry or replay reuses that draw. Replay answers from the log and never calls the model; re-running calls it again. A changed prompt changes the key and fails as a pin miss rather than silently re-drawing.
  - Belief store terms: *scope* (whose beliefs -- one per holder), *proposition* and *predicate* (the declared question a belief id names; arity fixed in code), *revise/expand/contract* (AGM belief-change operations: replace, add, retract), and *doxastica* (the separate library that stores beliefs; anagrapheus supplies the ids and the fold).
  - Identity layer: *decisions* as events; *establish* (the system mints an entity) vs *resolve* (discovering that two names are one entity); *merge* and *split*; *re-key* (moving beliefs keyed on an absorbed entity).
  - Chronicle: *sections* that consolidate a range of the log into prose citing that range; *consolidation*; *recency vs depth* (recent raw events vs distilled older history); anchors (replayable) vs prose (regenerated).
  - Prompt assembly: *grant* (the slices a role may read), *slice* (one source read at one position, remembering what it handed out), *part* (a structured piece of context with a register, rank and sources), *register* (how binding a part is), *budget* (the size limit and what may be shed).
  - Which decisions are the application's and which are the library's (see Writing Guidance).
- **Their world:** They build agents or simulations that run for many turns -- games, negotiations, interactive fiction (narrative-vm is the motivating case) -- where several agents hold private, revisable beliefs about the world and each other. Their pain is that runs are expensive and unrepeatable: a bug seen once cannot be reproduced, tests hit the network, and long histories overflow the context window. They already use pydantic-ai for the model call and want determinism and auditability without giving up real models.
- **How they found this library:** Either from the narrative-vm design work or from searching for deterministic replay / VCR-style recording for LLM agents. Alternatives they will have considered: HTTP cassette recording (breaks on any prompt change and records transport, not meaning), ad-hoc caching of model responses, or a vector-store memory layer. They typically read the negotiation example first, so docs should meet them there.

### Common Tasks
- Record a run to an event log and replay it offline: open a `SQLiteEventStore`, register events, run through `PinnedModel`, write a recording, then replay with `ReplayPins` and show that no model is reached.
- Pin model and tool calls: wrap the model in `PinnedModel`, wrap tools with `pinned_tool`, choose or customise a `CallPolicy` (what counts as "the same call" is the application's judgment).
- Keep per-agent beliefs as a fold: declare predicates in a `PredicateRegistry`, bind domain events to `Interpreter`s or emit `BeliefRevised`/`BeliefExpanded`/`BeliefContracted`, run `BeliefFold` via a `Projector`, read per scope.
- Resolve identity and re-key after a merge: `establish`/`alias`/`merge` decisions, `IdentityRegistry.rekeys_since`, `plan_rekeys`, then the application applies the plan.
- Consolidate history into chronicle sections: supply a `Summarizer` and unit boundaries to `ChronicleFold`; read `Recent` plus `Brief`; understand why rebuilding needs explicit acknowledgement.
- Assemble prompts from granted slices: define a grant, build `Part`s with sources, call `assemble` with a `Budget`, render, and pass the messages through `PinnedModel`.
- Evolve event schemas: register `Upcast`s so old logs still read; know what `UpcastGap` and `SchemaTooNew` mean.

### Writing Guidance for This Persona
- When explaining a layer, assume they know pydantic-ai and async Python but spell out the event-sourcing or belief term the first time it appears on each page, and link it to the Explanation page that owns it.
- Use precise technical vocabulary; do not soften it, but never use an internal design term the reader has not been given.
- Examples should be agents negotiating, NPCs in a game, or characters in interactive fiction -- multi-turn, multi-agent, with private beliefs. Prefer the UK/EU fisheries negotiation from `examples/negotiation/`.
- State plainly who owns each decision. **Application:** call policy (what makes two calls the same), predicate vocabularies, scope lists, identity judgments (whether two names are one entity), applying re-key plans, rendering, slot names, budgets, summariser prompts. **Library:** keys, pins, folds, positions, citation and grant checks.
- Show offline runs first (scripted or `FunctionModel`), then the real-provider variant; a real provider needs the `anthropic` or `openai` extra.

## Use Cases

### Replay an agent run exactly
**Problem:** An LLM agent's run is nondeterministic and costs money per call. A bug that appears on turn 37 of a long negotiation cannot be reproduced, and a test suite that calls the provider is slow, flaky and billed.
**What's possible:** Every model request and tool invocation goes through the pin ledger, keyed on the semantic content of what was asked (via the call policy, which drops timestamps and provider ids). The draw is recorded once in the event log, reused on retry within a unit of work, and answered from the log on replay. Streaming responses are pinned as their accumulated result.
**Outcome:** A recorded run replays on a fresh store with zero model calls, producing the same events. If the code now asks something different, replay raises `PinMiss` with a `Divergence` saying where, instead of quietly drawing a new answer.
**Relevant persona(s):** Python developers building long-running, replayable LLM agents (tasks: record/replay, pin calls, evolve schemas).
**Source:** user-provided

### Agents that believe different things
**Problem:** In a multi-agent simulation each agent must hold its own revisable beliefs -- including false ones -- and a prompt for one agent must never leak what only another agent knows. Prompt-building code that reaches into shared state makes such leaks silent.
**What's possible:** Beliefs are a fold over the log, held per scope (holder) in doxastica, with ids minted only from declared predicates. Prompts are assembled only from slices granted to the agent's role; every part cites its sources, and `assemble` rejects uncited or ungranted content.
**Outcome:** Two agents can disagree about the same proposition, the disagreement is measurable (`divergence`), and any assembled prompt can be audited to show it contains only what its agent was allowed to see.
**Relevant persona(s):** Python developers building long-running, replayable LLM agents (tasks: per-agent beliefs, identity/re-key, prompt assembly).
**Source:** user-provided

`use_cases_section` is false: these guide examples and framing, not a dedicated docs section.

## Tone: warm-businesslike

### Writing Rules
- Open each page with a 1-2 sentence introduction saying what the page covers and why the reader would want it.
- Give multiple examples per concept: the basic use first, then common variations (e.g. offline then real provider; default call policy then a custom one).
- Include "Common mistakes" or "Troubleshooting" sections where the API has sharp edges: unregistered event types, pin misses after a prompt change, minting at the wrong arity, rebuilding a generative fold, ungranted slices.
- Use transition sentences between major sections for reading flow.
- Use admonitions for tips, warnings and important notes.
- Warm but professional: no jokes, no first person, no casual asides.
- British English spelling throughout (behaviour, summarise, recognise, serialise), matching the code.
- Never cite the internal design notes, doc/section numbers ("doc 00 §4a", "note 07"), R-numbers or milestone names (M1-M9). Explain the idea itself. Source docstrings and example docstrings contain such citations; do not copy them into prose.

## Framework Preferences: mkdocs-material

Config: `mkdocs.yml` at repo root, `docs_dir: docs/src`. API reference pages are generated by `docs/scripts/gen_ref_pages.py` (mkdocs-gen-files + literate-nav) into `reference/`, one page per public module, rendered with mkdocstrings (Google docstrings, table sections, source shown, `_`-prefixed members filtered). Plugins: search, gen-files, literate-nav, section-index, mkdocstrings.

### Navigation Strategy
- Strategy: Sections as top tabs.
- Tabs: Overview, Tutorials, How-To Guides, Explanation, Reference (plus Changelog). Each tab maps to a section index page (`tutorials/index.md`, etc.); the Reference tab's index should introduce and link to the generated per-module API pages.
- MkDocs Material tabs are flat (no dropdowns) and come from the top-level `nav:` entries; the sidebar shows only the active tab's pages. Every tab needs at least 2 child pages.
- Section index pages must list each child with a bold link and a 1-sentence abstract written inline; each child page carries `title` and `description` frontmatter.
- Front page (index.md) is linked as "Overview" at the same level as the main docs sections. Use "Overview" not "Home". The current `nav:` says `Home`, has only Home / API Reference / Changelog, and the theme lacks `navigation.tabs`, `navigation.tabs.sticky` and `navigation.indexes` -- these must be added to `mkdocs.yml` for this strategy to render.
- Suggested Explanation pages, one per layer (the persona's never-assume list maps onto them): the log and projections, pinning and replay, beliefs, identity, the chronicle, prompt assembly, and what the library leaves to the application.

### Features to Use
- **Admonitions** (`admonition`, enabled): `tip` for recommended practice, `warning` for gotchas (pin misses, under-registration), `danger` for data-loss or irreversibility (rebuilding a generative fold, re-key semantics), `info` for context. Never stack them back-to-back.
- **Collapsible details** (`pymdownx.details`, enabled): `???` blocks for advanced or optional material -- e.g. how keys are derived, what streaming replay does not reproduce, schema-migration internals.
- **Content tabs** (`pymdownx.tabbed`, alternate style, enabled): for genuine alternatives of one thing -- offline scripted model vs real provider; `pip` vs `uv add`; default vs custom call policy; sync `resolve` vs `resolve_async`. Short labels.
- **Mermaid diagrams** (superfences custom fence, enabled): the log-and-projections architecture (one log, several folds), the record/replay sequence, the merge -> re-key plan -> application-applies flow, and grant -> slices -> parts -> assemble -> PinnedModel. Keep each diagram to one idea.
- **Code annotations** (`content.code.annotate`, enabled): for longer examples where inline comments would crowd the code -- e.g. marking which line is the application's decision versus the library's.
- **Code blocks:** always a language; `title="..."` for file names; `hl_lines` for the new lines only. Copy button is enabled.
- **Cross-references:** link every inline code mention to its generated reference page (`reference/anagrapheus/<subpackage>/<module>.md`), using the module where the symbol is defined, not the re-exporting package.

### Features NOT to Use
- `pymdownx.snippets` is enabled (base path is the repo root) but was not selected; use it only for the existing changelog include. Note: `docs/src/changelog.md` includes `CHANGELOG.md`, which does not exist, and `check_paths: true` will fail the build until it does.
- Not configured -- do not generate syntax for: tags plugin (frontmatter `tags` will not render), footnotes, abbreviations, definition lists, task lists, keys, mark/critic, math (arithmatex), social cards, blog, versioning, linked content tabs (`content.tabs.link`).
- No `mkdocs-mermaid2-plugin`; the superfences fence already handles Mermaid.
- No pages about development: quality gates, prek, justfile, tests or CI tiers (`DEVELOP.md`/`AGENTS.md` material) are maintainer-only.

## Notes for Downstream Agents
- The negotiation example lives outside the installed package (`examples/negotiation/`, run as `python -m examples.negotiation` from a repo checkout; a browser UI via `python -m examples.negotiation.ui`). Tutorials that use it must say the reader needs a clone of the repository. Its docstrings reference test files, R-numbers and milestones -- do not repeat those.
- Public docstrings in `provider`, `chronicle`, `assembly` and others cite "doc 00 §...", "note 07" and narrative-vm R-numbers; these will appear verbatim in the mkdocstrings reference. Prose pages must not echo them.
- `docs/src/index.md` is a stub ("TODO: Add usage examples") and the README is stale.
