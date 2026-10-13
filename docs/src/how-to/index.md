---
title: How-To Guides
description: Task-focused guides for recording and replaying agent runs, keeping per-agent beliefs, resolving identity, consolidating history and assembling prompts
---

# How-To Guides

These guides each solve one task, for readers who already have a pydantic-ai agent and know what they want it to do. Every guide runs offline with pydantic-ai's `FunctionModel`, and says which decisions are yours and which the library makes. If you are new to anagrapheus, start with the [Tutorials](../tutorials/index.md); for the ideas behind each layer, see [Explanation](../explanation/index.md).

## Pinning and replay

- **[How to Record a Run and Replay It Offline](record-and-replay.md)**: Write a run's model calls to a recording file and replay it later with no model reached.
- **[How to Pin Model and Tool Calls](pin-model-and-tool-calls.md)**: Wrap a model and its tools so each call is drawn once, reused on retry and answered on replay.
- **[How to Evolve Event Schemas with Upcasters](evolve-event-schemas.md)**: Change an event's payload shape while old logs and recordings still read and replay.

## Beliefs and identity

- **[How to Keep Per-Agent Beliefs as a Fold](per-agent-beliefs.md)**: Give each agent private, revisable beliefs derived from the log, and measure where two agents disagree.
- **[How to Resolve Identity and Re-key Beliefs After a Merge](identity-and-rekey.md)**: Record that two names are one entity and move every belief about the absorbed name onto the survivor.

## Long contexts

- **[How to Consolidate History into Chronicle Sections](chronicle-sections.md)**: Summarise each closed round into a cited section, and read recent events beside the summarised past.
- **[How to Assemble Prompts from Granted Slices](assemble-prompts.md)**: Build each agent's prompt only from what its role may read, with cited sources and a size budget.
