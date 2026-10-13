---
title: Explanation
description: Background on how anagrapheus is put together and why each layer works the way it does
---

# Explanation

These pages explain why anagrapheus is shaped the way it is: what each layer is for, what it guarantees, and which decisions it deliberately leaves to your application. They are for reading away from the keyboard. For step-by-step instructions, see the [How-To Guides](../how-to/index.md).

## Start here

- **[How the Layers Fit Together](architecture.md)**: one authoritative log, every other component a projection over it, and a clear line between what the library decides and what your application decides.

## The foundations

- **[The Event Log and Projections](event-log-and-projections.md)**: what an envelope, a fold, a projection and a position are, and why every read model can be thrown away and rebuilt.
- **[Pinning and Replay](pinning-and-replay.md)**: how model and tool calls are recorded under a key derived from what was asked, so a run replays offline or says exactly where it diverged.

## The layers built on them

- **[Beliefs](beliefs.md)**: per-holder beliefs as a fold over the log, with belief ids minted from declared predicates so two agents can disagree measurably.
- **[Identity](identity.md)**: how entities are established or resolved, why merges and splits are events, and why re-keying beliefs after a merge is planned by the library but applied by you.
- **[The Chronicle](chronicle.md)**: consolidating old history into prose sections that cite their range, and why this is the one projection that is better carried than rebuilt.
- **[Prompt Assembly](prompt-assembly.md)**: building a model's context only from slices its role was granted, with every part citing its sources and a budget that never drops what must hold.

!!! tip "Suggested reading order"
    Read the first three pages in order: the architecture, then the log, then pinning. The four layer pages after that stand on their own. Identity and beliefs make most sense read together, because a merge in one becomes a re-key in the other.
