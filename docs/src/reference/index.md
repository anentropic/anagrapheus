---
title: Reference
description: The generated API reference for anagrapheus, one page per module, grouped by subpackage.
---

# Reference

This section is the API reference, generated from the source code's docstrings. Each module has its own page, listing its classes, functions and constants with their full signatures.

Most applications import from a subpackage, for example `from anagrapheus.timeline import ReplayPins`. Each subpackage re-exports its public names, but each name is documented on the page of the module that defines it. So [`ReplayPins`][replay-pins] is documented under `anagrapheus.timeline.replay`, not under `anagrapheus.timeline`. The groups below say which module holds what.

The top-level package adds one function of its own, `anagrapheus.register_builtin_events(registry)`. It registers every event type the library emits, across all subpackages, into an [`EventTypeRegistry`][registry]. Call it once when you build your registry. Registering only some subpackages' events leads to an [`UnknownEventType`][unknown-event-type] error later, when a reader reaches an event type that was not registered.

## Timeline

**[`anagrapheus.timeline`](anagrapheus/timeline/sqlite.md)**: The append-only event log, its SQLite store, the event-type registry, folds and projections, schema upcasting, the pin ledger, replay and recordings.

- [`envelope`](anagrapheus/timeline/envelope.md): `DomainEvent`, the base class for your own events, and the envelope and stored forms the log keeps.
- [`registry`](anagrapheus/timeline/registry.md): `EventTypeRegistry` and `EventKey`, which name and version each event type.
- [`store`](anagrapheus/timeline/store.md): the store protocols, for a backend other than SQLite.
- [`sqlite`](anagrapheus/timeline/sqlite.md): `SQLiteEventStore`.
- [`folds`](anagrapheus/timeline/folds.md): `ProjectionFold`, `GenerativeFold`, `Projector` and `FoldDeterminism`.
- [`upcast`](anagrapheus/timeline/upcast.md): `Upcast`, `UpcastRegistry` and `EventReader`, which bring old event schemas forward as they are read.
- [`pins`](anagrapheus/timeline/pins.md): `PinLedger`, `pin_key`, the `PinResolver` protocol and the pin events.
- [`replay`](anagrapheus/timeline/replay.md): `ReplayPins`, `PinMiss` and `Divergence`.
- [`recording`](anagrapheus/timeline/recording.md): `write_recording`, `read_recording` and `pin_events`.
- [`migrations`](anagrapheus/timeline/migrations.md): `SchemaTooNew` and the store's schema migrations.
- [`errors`](anagrapheus/timeline/errors.md): `TimelineError` and its subclasses.

## Provider

**[`anagrapheus.provider`](anagrapheus/provider/model.md)**: Model and tool calls sent through the pin ledger, and the call policy that decides when two calls are the same call.

- [`model`](anagrapheus/provider/model.md): `PinnedModel`, which wraps any pydantic-ai model.
- [`tools`](anagrapheus/provider/tools.md): `pinned_tool`.
- [`policy`](anagrapheus/provider/policy.md): `CallPolicy` and the default `SemanticCallPolicy`.
- [`stream`](anagrapheus/provider/stream.md): `PinnedStreamedResponse`, the stream a replay rebuilds from a pinned response.
- [`errors`](anagrapheus/provider/errors.md): `ProviderError`.

## Beliefs

**[`anagrapheus.beliefs`](anagrapheus/beliefs/fold.md)**: Per-scope beliefs as a fold over the log, belief ids minted only from declared predicates, and the entity-to-belief index that re-keying reads.

- [`predicates`](anagrapheus/beliefs/predicates.md): `PredicateRegistry` and `Predicate`, the only way to mint a belief id.
- [`ops`](anagrapheus/beliefs/ops.md): `Revise`, `Expand`, `Contract` and the `Interpreter` type that turns your events into them.
- [`events`](anagrapheus/beliefs/events.md): `BeliefRevised`, `BeliefExpanded` and `BeliefContracted`.
- [`writer`](anagrapheus/beliefs/writer.md): `BeliefEvents`, which builds those events from a predicate and its entities.
- [`fold`](anagrapheus/beliefs/fold.md): `BeliefFold`.
- [`index`](anagrapheus/beliefs/index.md): `BeliefIndex`.
- [`store`](anagrapheus/beliefs/store.md) and [`sqlite`](anagrapheus/beliefs/sqlite.md): the index store protocol and `SQLiteBeliefIndexStore`.
- [`rekey`](anagrapheus/beliefs/rekey.md): `plan_rekeys` and `BeliefRekey`.
- [`divergence`](anagrapheus/beliefs/divergence.md): `divergence` and `Disagreement`.
- [`migrations`](anagrapheus/beliefs/migrations.md) and [`errors`](anagrapheus/beliefs/errors.md): schema migrations, and `BeliefError` and its subclasses.

## Identity

**[`anagrapheus.identity`](anagrapheus/identity/decisions.md)**: Entity identity as decisions recorded in the log, the registry folded from them, and the re-key rows a merge produces.

- [`decisions`](anagrapheus/identity/decisions.md): `establish`, `alias`, `merge`, `split` and `mint_entity_id`.
- [`events`](anagrapheus/identity/events.md): the decision events.
- [`fold`](anagrapheus/identity/fold.md): `IdentityFold`.
- [`registry`](anagrapheus/identity/registry.md): `IdentityRegistry`, including `rekeys_since`.
- [`ladder`](anagrapheus/identity/ladder.md): `Ladder`, your ordered list of stance words for how firmly an alias is held, used to rank candidates.
- [`vocabulary`](anagrapheus/identity/vocabulary.md): `IdentityVocabulary`, which maps your own event types onto the four identity decisions.
- [`store`](anagrapheus/identity/store.md) and [`sqlite`](anagrapheus/identity/sqlite.md): the identity store protocol and `SQLiteIdentityStore`.
- [`migrations`](anagrapheus/identity/migrations.md) and [`errors`](anagrapheus/identity/errors.md): schema migrations, and `IdentityError` and its subclasses.

## Chronicle

**[`anagrapheus.chronicle`](anagrapheus/chronicle/fold.md)**: Consolidation of log ranges into sections that cite the range they cover, read as recent events plus older history.

- [`fold`](anagrapheus/chronicle/fold.md): `ChronicleFold`, `Summarizer`, `Consolidation` and `section_id`.
- [`sections`](anagrapheus/chronicle/sections.md): `Section` and `Anchor`.
- [`reading`](anagrapheus/chronicle/reading.md): `Brief`, `Recent`, `Anchors`, `Prose`, `Retriever` and `substring_retriever`.
- [`store`](anagrapheus/chronicle/store.md) and [`sqlite`](anagrapheus/chronicle/sqlite.md): the chronicle store protocol, with export and load, and `SQLiteChronicleStore`.
- [`migrations`](anagrapheus/chronicle/migrations.md): schema migrations.

## Assembly

**[`anagrapheus.assembly`](anagrapheus/assembly/assemble.md)**: Prompts built only from the slices of state a role was granted, with parts that cite their sources and a budget that never sheds a binding part.

- [`slices`](anagrapheus/assembly/slices.md): `EventsSlice`, `LogSlice`, `BeliefSlice`, `ChronicleSlice`, `Given` and `handed_out`.
- [`parts`](anagrapheus/assembly/parts.md): `Part`, `Register`, `Source` and `Context`.
- [`assemble`](anagrapheus/assembly/assemble.md): `assemble` and `Budget`.
- [`render`](anagrapheus/assembly/render.md): `render_labelled`.
- [`errors`](anagrapheus/assembly/errors.md): `AssemblyError` and its subclasses, such as `NotGranted` and `BudgetExceeded`.

[replay-pins]: anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[registry]: anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry
[unknown-event-type]: anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.UnknownEventType

<!-- INTERNAL NOTES FOR EDITOR - DO NOT INCLUDE IN RENDERED OUTPUT
## Page Classification
- Type: index (section index for the generated reference)
- Reference used: dedicated template (Section Index Pages rules)

## Source References
- docs/scripts/gen_ref_pages.py: writes reference/<module path>.md for every src/**/*.py except __init__/__main__/conftest and any `_`-prefixed part, plus reference/SUMMARY.md. It never writes reference/index.md, so this hand-written file is not overwritten.
- Module list taken from `find src -name '*.py'` with the same filters (49 pages). Every link here points at one of them.
- Symbol-to-module placement checked by grepping `^(class|def) <name>` in src/.

## Accuracy Notes
- `Interpreter` is a type alias in beliefs/ops.py (`type Interpreter = Callable[[Envelope, DomainEvent], Sequence[BeliefOp]]`), not a class.
- The top-level anagrapheus/__init__.py has no generated page, so `register_builtin_events` is described here in prose and not linked.
- Section headings (Timeline, Provider, ...) produce anchors #timeline, #provider, #beliefs, #identity, #chronicle, #assembly; docs/src/index.md links to them.
- Subpackage bold links go to the subpackage's central module because no per-subpackage page exists.

## Known Limitations
- mkdocs.yml nav must combine this index with the literate-nav reference/SUMMARY.md (orchestrator's job).
- Source docstrings cite internal design notes and R-numbers; these show verbatim on the generated pages.

## API Reference Links Used
- anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
- anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry
- anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.UnknownEventType
-->
