---
title: Reference
description: The generated API reference for anagrapheus, one page per module, grouped by subpackage.
---

# Reference

This section is the API reference, generated from the source code's docstrings. Each module has its own page, listing its classes, functions and constants with their full signatures.

Most applications import from a subpackage, for example `from anagrapheus.timeline import ReplayPins`. Each subpackage re-exports its public names, but each name is documented on the page of the module that defines it. So [`ReplayPins`][replay-pins] is documented under `anagrapheus.timeline.replay`, not under `anagrapheus.timeline`. The groups below say which module holds what.

The top-level package adds one function of its own, [`anagrapheus.register_builtin_events(registry)`](anagrapheus/index.md#anagrapheus.register_builtin_events). It registers every event type the library emits, across all subpackages, into an [`EventTypeRegistry`][registry]. Call it once when you build your registry. Registering only some subpackages' events leads to an [`UnknownEventType`][unknown-event-type] error later, when a reader reaches an event type that was not registered.

## Timeline

**[`anagrapheus.timeline`](anagrapheus/timeline/sqlite.md)**: The append-only event log, its SQLite store, the event-type registry, folds and projections, schema upcasting, the pin ledger, replay and recordings.

- [`envelope`](anagrapheus/timeline/envelope.md): [`DomainEvent`](anagrapheus/timeline/envelope.md#anagrapheus.timeline.envelope.DomainEvent), the base class for your own events, and the envelope and stored forms the log keeps.
- [`registry`](anagrapheus/timeline/registry.md): [`EventTypeRegistry`](anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry) and [`EventKey`](anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventKey), which name and version each event type.
- [`store`](anagrapheus/timeline/store.md): the store protocols, for a backend other than SQLite.
- [`sqlite`](anagrapheus/timeline/sqlite.md): [`SQLiteEventStore`](anagrapheus/timeline/sqlite.md#anagrapheus.timeline.sqlite.SQLiteEventStore).
- [`folds`](anagrapheus/timeline/folds.md): [`ProjectionFold`](anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.ProjectionFold), [`GenerativeFold`](anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.GenerativeFold), [`OwnsPosition`](anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.OwnsPosition), [`PreparesSynchronously`](anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.PreparesSynchronously), [`Projector`](anagrapheus/timeline/folds.md#anagrapheus.timeline.folds.Projector) and `FoldDeterminism`.
- [`upcast`](anagrapheus/timeline/upcast.md): `Upcast`, [`UpcastRegistry`](anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.UpcastRegistry) and [`EventReader`](anagrapheus/timeline/upcast.md#anagrapheus.timeline.upcast.EventReader), which bring old event schemas forward as they are read.
- [`pins`](anagrapheus/timeline/pins.md): [`PinLedger`](anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger), [`pin_key`](anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.pin_key), the [`PinResolver`](anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinResolver) protocol and the pin events.
- [`replay`](anagrapheus/timeline/replay.md): [`ReplayPins`](anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins), [`PinMiss`](anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.PinMiss) and [`Divergence`](anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.Divergence).
- [`recording`](anagrapheus/timeline/recording.md): [`write_recording`](anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.write_recording), [`read_recording`](anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.read_recording), [`pin_events`](anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.pin_events), [`Recording`](anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.Recording) and [`RecordingError`](anagrapheus/timeline/recording.md#anagrapheus.timeline.recording.RecordingError).
- [`migrations`](anagrapheus/timeline/migrations.md): [`SchemaTooNew`](anagrapheus/timeline/migrations.md#anagrapheus.timeline.migrations.SchemaTooNew) and the store's schema migrations.
- [`errors`](anagrapheus/timeline/errors.md): [`TimelineError`](anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.TimelineError) and its subclasses.

## Provider

**[`anagrapheus.provider`](anagrapheus/provider/model.md)**: Model and tool calls sent through the pin ledger, and the call policy that decides when two calls are the same call.

- [`model`](anagrapheus/provider/model.md): [`PinnedModel`](anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel), which wraps any pydantic-ai model.
- [`tools`](anagrapheus/provider/tools.md): [`pinned_tool`](anagrapheus/provider/tools.md#anagrapheus.provider.tools.pinned_tool).
- [`policy`](anagrapheus/provider/policy.md): [`CallPolicy`](anagrapheus/provider/policy.md#anagrapheus.provider.policy.CallPolicy) and the default [`SemanticCallPolicy`](anagrapheus/provider/policy.md#anagrapheus.provider.policy.SemanticCallPolicy).
- [`stream`](anagrapheus/provider/stream.md): [`PinnedStreamedResponse`](anagrapheus/provider/stream.md#anagrapheus.provider.stream.PinnedStreamedResponse), the stream a replay rebuilds from a pinned response.
- [`errors`](anagrapheus/provider/errors.md): [`ProviderError`](anagrapheus/provider/errors.md#anagrapheus.provider.errors.ProviderError).

## Beliefs

**[`anagrapheus.beliefs`](anagrapheus/beliefs/fold.md)**: Per-scope beliefs as a fold over the log, belief ids minted only from declared predicates, and the entity index (entity to belief ids) that re-keying reads.

- [`predicates`](anagrapheus/beliefs/predicates.md): [`PredicateRegistry`](anagrapheus/beliefs/predicates.md#anagrapheus.beliefs.predicates.PredicateRegistry) and [`Predicate`](anagrapheus/beliefs/predicates.md#anagrapheus.beliefs.predicates.Predicate), the only way to mint a belief id.
- [`ops`](anagrapheus/beliefs/ops.md): [`Revise`](anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Revise), [`Expand`](anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Expand), [`Contract`](anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.Contract), the `Interpreter` type that turns your events into them, and [`StateRef`](anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.StateRef), which names a belief state an edge or a prompt part cites.
- [`events`](anagrapheus/beliefs/events.md): [`BeliefRevised`](anagrapheus/beliefs/events.md#anagrapheus.beliefs.events.BeliefRevised), [`BeliefExpanded`](anagrapheus/beliefs/events.md#anagrapheus.beliefs.events.BeliefExpanded) and [`BeliefContracted`](anagrapheus/beliefs/events.md#anagrapheus.beliefs.events.BeliefContracted).
- [`writer`](anagrapheus/beliefs/writer.md): [`BeliefEvents`](anagrapheus/beliefs/writer.md#anagrapheus.beliefs.writer.BeliefEvents), which builds those events from a predicate and its entities.
- [`fold`](anagrapheus/beliefs/fold.md): [`BeliefFold`](anagrapheus/beliefs/fold.md#anagrapheus.beliefs.fold.BeliefFold).
- [`index`](anagrapheus/beliefs/index-module.md): [`BeliefIndex`](anagrapheus/beliefs/index-module.md#anagrapheus.beliefs.index.BeliefIndex).
- [`store`](anagrapheus/beliefs/store.md) and [`sqlite`](anagrapheus/beliefs/sqlite.md): the index store protocol and [`SQLiteBeliefIndexStore`](anagrapheus/beliefs/sqlite.md#anagrapheus.beliefs.sqlite.SQLiteBeliefIndexStore).
- [`rekey`](anagrapheus/beliefs/rekey.md): [`plan_rekeys`](anagrapheus/beliefs/rekey.md#anagrapheus.beliefs.rekey.plan_rekeys) and [`BeliefRekey`](anagrapheus/beliefs/rekey.md#anagrapheus.beliefs.rekey.BeliefRekey).
- [`divergence`](anagrapheus/beliefs/divergence.md): [`divergence`](anagrapheus/beliefs/divergence.md#anagrapheus.beliefs.divergence.divergence) and [`Disagreement`](anagrapheus/beliefs/divergence.md#anagrapheus.beliefs.divergence.Disagreement).
- [`migrations`](anagrapheus/beliefs/migrations.md) and [`errors`](anagrapheus/beliefs/errors.md): schema migrations, and [`BeliefError`](anagrapheus/beliefs/errors.md#anagrapheus.beliefs.errors.BeliefError) and its subclasses.

## Identity

**[`anagrapheus.identity`](anagrapheus/identity/decisions.md)**: Entity identity as decisions recorded in the log, the registry folded from them, and the re-key rows a merge produces.

- [`decisions`](anagrapheus/identity/decisions.md): [`establish`](anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.establish), [`alias`](anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.alias), [`merge`](anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.merge), [`split`](anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.split) and [`mint_entity_id`](anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.mint_entity_id).
- [`events`](anagrapheus/identity/events.md): the decision events.
- [`fold`](anagrapheus/identity/fold.md): [`IdentityFold`](anagrapheus/identity/fold.md#anagrapheus.identity.fold.IdentityFold).
- [`registry`](anagrapheus/identity/registry.md): [`IdentityRegistry`](anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry), including [`rekeys_since`](anagrapheus/identity/registry.md#anagrapheus.identity.registry.IdentityRegistry.rekeys_since).
- [`ladder`](anagrapheus/identity/ladder.md): [`Ladder`](anagrapheus/identity/ladder.md#anagrapheus.identity.ladder.Ladder), your ordered list of stance tokens for how firmly an alias is held, used to rank candidates.
- [`vocabulary`](anagrapheus/identity/vocabulary.md): [`IdentityVocabulary`](anagrapheus/identity/vocabulary.md#anagrapheus.identity.vocabulary.IdentityVocabulary), which maps your own event types onto the four identity decisions.
- [`store`](anagrapheus/identity/store.md) and [`sqlite`](anagrapheus/identity/sqlite.md): the identity store protocol and [`SQLiteIdentityStore`](anagrapheus/identity/sqlite.md#anagrapheus.identity.sqlite.SQLiteIdentityStore).
- [`migrations`](anagrapheus/identity/migrations.md) and [`errors`](anagrapheus/identity/errors.md): schema migrations, and [`IdentityError`](anagrapheus/identity/errors.md#anagrapheus.identity.errors.IdentityError) and its subclasses.

## Chronicle

**[`anagrapheus.chronicle`](anagrapheus/chronicle/fold.md)**: Consolidation of log ranges into sections that cite the range they cover, read as recent events plus older history.

- [`fold`](anagrapheus/chronicle/fold.md): [`ChronicleFold`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.ChronicleFold), [`Summarizer`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.Summarizer), [`Consolidation`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.Consolidation), the unit boundaries [`UnitBoundary`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.UnitBoundary) and [`EventTypes`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.EventTypes), and [`section_id`](anagrapheus/chronicle/fold.md#anagrapheus.chronicle.fold.section_id).
- [`sections`](anagrapheus/chronicle/sections.md): [`Section`](anagrapheus/chronicle/sections.md#anagrapheus.chronicle.sections.Section) and [`Anchor`](anagrapheus/chronicle/sections.md#anagrapheus.chronicle.sections.Anchor).
- [`reading`](anagrapheus/chronicle/reading.md): [`Brief`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Brief), [`Recent`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Recent), [`Anchors`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Anchors), [`Prose`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Prose), [`Retriever`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.Retriever) and [`substring_retriever`](anagrapheus/chronicle/reading.md#anagrapheus.chronicle.reading.substring_retriever).
- [`store`](anagrapheus/chronicle/store.md) and [`sqlite`](anagrapheus/chronicle/sqlite.md): the chronicle store protocol, with export and load, and [`SQLiteChronicleStore`](anagrapheus/chronicle/sqlite.md#anagrapheus.chronicle.sqlite.SQLiteChronicleStore).
- [`migrations`](anagrapheus/chronicle/migrations.md): schema migrations.

## Assembly

**[`anagrapheus.assembly`](anagrapheus/assembly/assemble.md)**: Prompts built only from the slices of state a role was granted, with parts that cite their sources and a budget that never sheds a binding part.

- [`slices`](anagrapheus/assembly/slices.md): [`EventsSlice`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.EventsSlice), [`LogSlice`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.LogSlice), [`BeliefSlice`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.BeliefSlice), [`ChronicleSlice`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.ChronicleSlice), [`Given`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.Given) and [`handed_out`](anagrapheus/assembly/slices.md#anagrapheus.assembly.slices.handed_out).
- [`parts`](anagrapheus/assembly/parts.md): [`Part`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.Part), [`Register`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.Register), `Source` with its variants [`EventRef`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.EventRef), [`SectionRef`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.SectionRef), [`GivenRef`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.GivenRef) and the belief layer's [`StateRef`](anagrapheus/beliefs/ops.md#anagrapheus.beliefs.ops.StateRef), and [`Context`](anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.Context).
- [`assemble`](anagrapheus/assembly/assemble.md): [`assemble`](anagrapheus/assembly/assemble.md#anagrapheus.assembly.assemble.assemble) and [`Budget`](anagrapheus/assembly/assemble.md#anagrapheus.assembly.assemble.Budget).
- [`render`](anagrapheus/assembly/render.md): [`render_labelled`](anagrapheus/assembly/render.md#anagrapheus.assembly.render.render_labelled).
- [`errors`](anagrapheus/assembly/errors.md): [`AssemblyError`](anagrapheus/assembly/errors.md#anagrapheus.assembly.errors.AssemblyError) and its subclasses, such as [`NotGranted`](anagrapheus/assembly/errors.md#anagrapheus.assembly.errors.NotGranted) and [`BudgetExceeded`](anagrapheus/assembly/errors.md#anagrapheus.assembly.errors.BudgetExceeded).

[replay-pins]: anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[registry]: anagrapheus/timeline/registry.md#anagrapheus.timeline.registry.EventTypeRegistry
[unknown-event-type]: anagrapheus/timeline/errors.md#anagrapheus.timeline.errors.UnknownEventType
