---
title: Touring the Negotiation Example
description: Run the two-party fisheries negotiation offline, then inspect its log, pins, beliefs, chronicle, prompts and replay one layer at a time.
difficulty: intermediate
---

# Touring the Negotiation Example

The repository ships a worked example: the EU and the UK negotiate a fisheries clause over four rounds, each side holding private beliefs about what the other wants. It uses every layer of anagrapheus at once. In this tutorial you run it offline, read what it prints, and then write a short script that opens up each layer in turn.

By the end you will have seen:

- the event log a whole negotiation leaves behind, and the pins inside it
- each party's beliefs, kept in separate *scopes* (one per holder) and built by a fold over the log
- the chronicle's sections, each consolidating one round and citing the log range it covers
- an assembled prompt whose parts carry a *register* (how binding the part is) and their sources
- a merge of two names into one entity, and the re-key plan it produces
- the whole negotiation replayed on a fresh store, with no model reached

**Time:** about 30 minutes.
**Prerequisites:** Python 3.14 or later, [git](https://git-scm.com/), and [uv](https://docs.astral.sh/uv/). Working through [Getting Started](getting-started.md) first helps: this tutorial assumes you know what pinning and replay are.

## Set up a clone

The example is not part of the installed package; it lives in the repository's `examples/negotiation/` directory. Clone the repository and install its dependencies:

```bash
git clone https://github.com/anentropic/anagrapheus.git
cd anagrapheus
uv sync
```

Every command from here on runs from the root of the clone.

## Step 1: Run the negotiation

Run the example with no arguments. With no model named, it plays both sides with a scripted model that runs offline:

```bash
uv run python -m examples.negotiation
```

After pydantic-ai's banner (set `PYDANTIC_AI_NO_BANNER=1` to hide it), you should see:

```text
                  lineup  eu.propose=function:scripted, eu.update=function:scripted, uk.propose=function:scripted, uk.update=function:scripted, summarize=function:scripted
                  tabled  fisheries is on the table
                  seeded  the EU wants a settlement; the UK will privately concede
            round 1 / eu  offered hold firm (we read their position as unknown and their bottom line as unknown); concluded red_line(uk) = concede
            round 1 / uk  offered hold firm (we read their position as unknown and their bottom line as unknown); concluded red_line(eu) = hold_firm
          round 1 / leak  uk let slip 'concede' — eu believes eu and uk heard it; uk believes nobody heard it
       round 1 / depth 2  EU reads 'concede'; UK imagines 'hold_firm' — the UK is wrong about what the EU concluded
            round 2 / eu  offered hold firm (we read their position as concede and their bottom line as concede); concluded red_line(uk) = hold_firm
            round 2 / uk  offered hold firm (we read their position as hold_firm and their bottom line as hold_firm); concluded red_line(eu) = hold_firm
       round 2 / depth 2  EU reads 'hold_firm'; UK imagines 'hold_firm' — agreed
            round 3 / eu  offered hold firm (we read their position as hold_firm and their bottom line as hold_firm); concluded red_line(uk) = hold_firm
            round 3 / uk  offered hold firm (we read their position as hold_firm and their bottom line as hold_firm); concluded red_line(eu) = never
          round 3 / leak  eu let slip 'never' — eu believes eu and uk heard it, uk believes eu and uk heard it
       round 3 / depth 2  EU reads 'hold_firm'; UK imagines 'hold_firm' — agreed
            round 4 / eu  offered hold firm (we read their position as hold_firm and their bottom line as hold_firm); concluded red_line(uk) = concede
            round 4 / uk  offered concede (we read their position as never and their bottom line as never); concluded red_line(eu) = hold_firm
       round 4 / depth 2  EU reads 'concede'; UK imagines 'concede' — agreed
        eu's model of uk  20 belief(s) over 10 event(s) — derived, never stored
        uk's model of eu  19 belief(s) over 9 event(s) — derived, never stored

replayed 4 rounds on a fresh store in strict mode: every draw answered from the log, none from a model
```

A full negotiation ran and then replayed. Here is what each line reports.

- **`lineup`** names the model behind each *role*. There are three roles: `propose` makes an offer, `update` reads what happened and concludes one thing about the other side, and `summarize` writes the chronicle. Each party has its own `propose` and `update` slots.
- **`tabled`** and **`seeded`** set the scene. The clause becomes an entity, and each party is given what it privately wants: the EU wants a settlement, and the UK is willing to concede but will not say so.
- **`round N / eu`** and **`round N / uk`** are the two pinned calls each party makes per round. The offer comes from `propose`; the `concluded red_line(...)` part is the belief that `update` wrote about the other side's bottom line.
- **`round N / leak`** is a remark one party let slip, and what each party *believes* about who heard it. In round 1 the UK believes nobody heard; the EU believes both sides did. Those beliefs can be wrong, and the UK's is.
- **`round N / depth 2`** compares two levels of belief: what the EU has concluded about the UK, and what the UK imagines the EU has concluded. After round 1 they differ, because the UK thinks its slip went unheard.
- **`eu's model of uk`** is what the EU believes the UK wants, rebuilt from the EU's own beliefs at the moment it is asked for and then thrown away. A belief about another party's beliefs is never stored.
- The last line is the replay: the same negotiation run again on a new, empty store, with every model request answered from the pins in the first run's log.

## Step 2: Write a tour script

The printed story is built from the objects the example returns. To see each layer directly, write a script that runs the negotiation itself and then asks each layer a question.

Create `tour.py` in the root of the clone:

```python title="tour.py"
import tempfile
import uuid
from collections import Counter
from pathlib import Path

from doxastica import BeliefFilter
from examples.negotiation.agent import proposing, render_proposing
from examples.negotiation.rekeying import apply_rekeys
from examples.negotiation.scopes import held
from examples.negotiation.scripted import scripted
from examples.negotiation.story import CLAUSE, negotiate

from anagrapheus.beliefs import BeliefIndex, plan_rekeys
from anagrapheus.identity import merge
from anagrapheus.timeline import ReplayPins, pin_events

with tempfile.TemporaryDirectory() as directory:
    run = negotiate(Path(directory) / "summit.db", models=scripted())
    run.commit()
    table = run.table
    events = list(table.store.read())

    print("== the log")
    for (namespace, kind), count in sorted(
        Counter((e.envelope.namespace, e.envelope.type) for e in events).items()
    ):
        print(f"{count:4}  {namespace}.{kind}")

    print("\n== the pins")
    pins = list(pin_events(events))
    print(Counter(p.payload["kind"] for p in pins))

    print("\n== beliefs, per scope")
    for scope in table.scopes():
        beliefs = table.core.query_scope(scope, BeliefFilter())
        names = Counter(table.predicates.parse(b.belief_id).name for b in beliefs)
        print(f"{scope:16}  {dict(names)}")
    for gap in table.own_gap("eu"):
        print(f"the EU says {gap.held!r} and wants {gap.reference!r}")

    print("\n== the chronicle")
    for section in table.view("eu").summaries():
        anchor = section.anchor
        print(f"seq {anchor.from_seq}-{anchor.to_seq}: {section.prose}")

    print("\n== an assembled prompt")
    context = proposing(table.view("eu").settled(), "uk", CLAUSE)
    for part in context.parts[:6]:
        print(f"{part.slot:10} {part.register.name:10} {len(part.sources)} source(s)")
    print(render_proposing(context).splitlines()[0])

    print("\n== a merge and its re-key")
    hmg = table.seat("HMG")
    clause = table.clauses[CLAUSE]
    table.believe(held("eu"), "position", (hmg, clause), "never", table.tabled_at[CLAUSE])
    table.append(merge(table.types, kept=table.seats["uk"], absorbed=hmg))
    plans = plan_rekeys(
        table.identity.rekeys_since(0), BeliefIndex(table.index_store), table.predicates
    )
    for plan in plans:
        print(f"{plan.belief_id} -> {plan.candidates}")
    print(apply_rekeys(table, plans, source_event=uuid.uuid7()))
    table.close()

    print("\n== the replay")
    replay = negotiate(
        Path(directory) / "replay.db",
        models=scripted(),
        resolver=lambda fresh: ReplayPins.from_events(events, fresh.types),
    )
    print(f"replayed {len(replay.rounds)} rounds; divergence: {replay.pins.divergence}")
    replay.table.close()
```

Run it:

```bash
uv run python tour.py
```

The next steps go through its output one section at a time.

## Step 3: Read the log and the pins

The first two sections of the output are:

```text
== the log
  56  anagrapheus.BeliefRevised
  29  anagrapheus.PinPromoted
   3  anagrapheus.established
   1  negotiation.ClauseTabled
   8  negotiation.PositionStated
   4  negotiation.RoundClosed
   2  negotiation.SideRemarkOverheard

== the pins
Counter({'model_call': 28, 'tool_call': 1})
```

Every event in the log has a namespace. The `negotiation` events are the example's own vocabulary: a clause tabled, positions stated, remarks overheard, rounds closed. The `anagrapheus` events are the library's: belief changes, identity decisions (`established` is one per party and one for the clause), and promoted pins. The library's namespace is reserved, so the two vocabularies can never collide.

The 29 pins are what made the replay possible. 28 are model requests. Each party makes two calls per round, and each round ends with one summary call. Every `propose` call also takes two requests, because the agent first asks for a tool and then answers once the tool has returned: 16 for offers, 8 for updates and 4 for summaries.

The remaining pin is a tool call. Each `propose` call looks the clause up in an archive through a tool wrapped with [`pinned_tool`][pinned-tool]. The tool's key is built from its name and arguments, and the arguments are the same every time, so the archive was reached once and every later lookup reused that pin. A replay sees the answer the run saw, not whatever the archive says now.

## Step 4: Read the beliefs

```text
== beliefs, per scope
party:eu:held     {'position': 1, 'witnessed': 20, 'red_line': 1}
party:eu:stated   {'position': 1}
party:uk:held     {'position': 1, 'witnessed': 18, 'red_line': 1}
party:uk:stated   {'position': 1}
the EU says 'hold_firm' and wants 'settle'
```

Each party has two scopes. `party:eu:held` is what the EU actually believes and wants, and only the EU's agent can read it. `party:eu:stated` is what the EU has said in the room, and anyone can read it. The example's application code chose this naming; the library only requires that each holder has its own scope.

Each belief has a *predicate*, the declared question its id names:

- `position` is a party's position on a clause.
- `red_line` is a party's conclusion about how far the other side will move.
- `witnessed` records which events a party believes another party saw. These are the beliefs from which "the EU's model of the UK" is rebuilt.

None of these beliefs were written directly. The example appends [`BeliefRevised`][belief-revised] events, and a [`BeliefFold`][belief-fold] folds them into the belief store, so the store can be dropped and rebuilt from the log at any time.

The last line compares two of the EU's own scopes with [`divergence`][divergence]: the EU says `hold_firm` and wants `settle`. Only the EU could ask this question, because nobody else can read its `held` scope.

## Step 5: Read the chronicle

```text
== the chronicle
seq 1-24: the clause 'fisheries' was tabled; eu stated hold_firm; uk stated hold_firm; round 1 closed
seq 25-39: eu stated hold_firm; uk stated hold_firm; round 2 closed
seq 40-59: eu stated hold_firm; uk stated hold_firm; round 3 closed
seq 60-74: eu stated hold_firm; uk stated concede; round 4 closed
```

The chronicle consolidates each closed round into one *section*: prose that cites the range of the log it covers, from one sequence number to another. Each `RoundClosed` event marks where one round ends and the next begins. Writing the prose is a model call (here, the scripted `summarize` role), and it is pinned like every other call.

Later prompts read the recent raw events plus these older sections instead of the whole log, which is how a long negotiation fits a context window. Notice that the sections never mention the overheard remarks. Every party reads the same chronicle, so the summariser is only allowed to see events every party saw.

## Step 6: Read an assembled prompt

```text
== an assembled prompt
ask        binding    0 source(s)
clause     considered 0 source(s)
ours       considered 1 source(s)
theirs     considered 1 source(s)
modelled   considered 0 source(s)
concluded  considered 1 source(s)
You are negotiating for eu. Given the brief, propose whether to concede this clause.
```

This is the context the EU's `propose` call is built from, before it is rendered to text. Prompts are put together with [`assemble`][assemble] from the state the role was *granted*, and nothing else. Here the grant is the EU's own view of the table.

Each [`Part`][part] has a slot name, a [`Register`][register] and its sources:

- `binding` parts must reach the model, and a budget can never drop them. The `ask` is binding.
- `considered` parts are advisory, so a budget may shed them, lowest rank first. The brief lines are considered.
- The `ours`, `theirs` and `concluded` lines each cite the belief they were read from. The `modelled` line cites nothing directly, because it was derived from many beliefs rather than read from one.

After the sixth part come the chronicle's sections, as `generated` parts: prose a model wrote, labelled as such. The last line of the output is the first line of the rendered prompt. Rendering is the application's code, not the library's.

## Step 7: Merge two names and re-key

```text
== a merge and its re-key
v1:negotiation:position(01a1329a-9440-…,01a1329a-931f-…) -> ('v1:negotiation:position(01a1329a-931e-…,01a1329a-931f-…)',)
[Applied(scope='party:eu:held', moved=1, undecided=())]
```

Your ids will differ from these, and the ellipses here shorten them.

This part of the script stages a common identity problem. A new party, `HMG`, is seated, and the EU forms a belief about HMG's position on the clause. Then the application decides that HMG and the UK are the same negotiator, and appends a [`merge`][merge] decision with the UK as the entity that is kept and HMG as the one absorbed.

A belief id names its entities, so the EU's belief is now keyed on an entity that names nothing. [`plan_rekeys`][plan-rekeys] reads the merge from the identity layer and the entity index, a [`BeliefIndex`][belief-index] from each entity to the belief ids that name it, and returns one plan: this belief id becomes that one, with HMG's id replaced by the UK's. The first id in each pair is the party; the second, unchanged, is the clause.

The library stops at the plan. Carrying it out is `apply_rekeys`, which is the example's own code in `examples/negotiation/rekeying.py`: it visits every scope the application knows about, and moves each affected belief by appending a revise for the new id and a contract (retraction) for the old one. Here it moved one belief, in the EU's `held` scope.

## Step 8: Read the replay

```text
== the replay
replayed 4 rounds; divergence: None
```

The script ran `negotiate` again on a new database, this time with a [`ReplayPins`][replay-pins] built from the first run's events. All 29 pins were answered from the log. `divergence` is `None`, which means no request missed. In strict mode, the default, the first miss would have raised [`PinMiss`][pin-miss] before reaching a model.

## Step 9: Try a real model (optional)

The same command takes real models. Each `--model` is a pydantic-ai model string. A bare one fills every role, `role=` overrides one role, and `party.role=` overrides one party's role. This needs an API key for the provider; `uv sync` already installed the Anthropic and OpenAI SDKs.

```bash
export ANTHROPIC_API_KEY=...  # your key
uv run python -m examples.negotiation --model anthropic:claude-haiku-5-5 --log-level INFO
```

`--log-level INFO` logs every model and tool call to stderr, with whether it was drawn or reused from a pin. The negotiation will play out differently, because a real model reads the brief its own way. The replay line at the end is the same claim as before, now against a provider: every draw answered from the log, none from a model. If the provider's answer cannot be recorded (for example, a field the round requires is missing even after a retry), the command says so and exits.

!!! warning "Real runs cost money"

    One negotiation makes 28 or more model requests. Use a small, cheap model for a first run.

## Step 10: Watch it in a browser (optional)

The example also has a small local web page that shows the log, what each party holds, the derived models and every model call as the negotiation runs:

```bash
uv run python -m examples.negotiation.ui
```

It serves on `http://127.0.0.1:8765/` and opens a browser tab. Press **Run** to start a negotiation. It takes the same `--model` options, plus `--port`, `--no-browser` and `--run` (start a negotiation as soon as the server is up). It only listens on a loopback address, because it has no authentication and a run spends your provider keys.

## Where each layer lives in the example

Use this table to find the code behind each part of the tour.

| Layer | Where the example uses it |
|---|---|
| Log, registry and folds | `table.py`: `Table.open` is the composition root. It opens one store, registers the library's and the example's events, and wires the identity, belief and chronicle folds into projectors. |
| Event vocabulary and predicates | `domain.py`: the negotiation's own events and its predicate registry. |
| Pinned model and tool calls | `agent.py`: each `Negotiator` wraps its models in [`PinnedModel`][pinned-model] and its archive lookup in `pinned_tool`. `story.py` chooses between a [`PinLedger`][pin-ledger] and a replay resolver. |
| Beliefs and scopes | `scopes.py` names the scopes; `table.py` appends belief events; `view.py` limits what one party can read. |
| Beliefs about beliefs | `nesting.py`: rebuilt on demand from a party's own `witnessed` beliefs, never stored. |
| Identity and re-keying | `table.py` establishes parties and clauses as entities; `rekeying.py` applies a re-key plan across every scope. |
| Chronicle | `story.py`: `RoundSummary` is the summariser, and `Table.brief` reads recent events plus sections. |
| Prompt assembly | `agent.py`: `proposing` and `reading` assemble each call's context; `render_proposing` and `render_reading` turn it into text. |
| Offline model | `scripted.py`: reads the brief it is given and decides from it, so changing a belief changes the offer. |

## What you've learned

You have run a multi-agent negotiation and taken it apart layer by layer. You have seen:

- a log holding both the application's events and the library's, with the pins that make replay possible
- beliefs kept per scope and built by a fold, with private and public scopes for each party
- chronicle sections that cite the log range they consolidate
- a prompt assembled from granted state, where each part has a register and its sources
- a merge producing a re-key plan, applied by application code
- a strict replay on a fresh store that reached no model

## Next steps

- [Keep per-agent beliefs as a fold](../how-to/per-agent-beliefs.md) to set up predicates, scopes and a belief fold in your own application.
- [Resolve identity and re-key beliefs after a merge](../how-to/identity-and-rekey.md) for the merge, plan and apply steps in detail.
- [Assemble prompts from granted slices](../how-to/assemble-prompts.md) to build contexts like the one in Step 6.
- [How the layers fit together](../explanation/architecture.md) for why the log is the only authoritative record, and which decisions are left to the application.

[pinned-tool]: ../reference/anagrapheus/provider/tools.md#anagrapheus.provider.tools.pinned_tool
[belief-fold]: ../reference/anagrapheus/beliefs/fold.md#anagrapheus.beliefs.fold.BeliefFold
[divergence]: ../reference/anagrapheus/beliefs/divergence.md#anagrapheus.beliefs.divergence.divergence
[assemble]: ../reference/anagrapheus/assembly/assemble.md#anagrapheus.assembly.assemble.assemble
[part]: ../reference/anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.Part
[register]: ../reference/anagrapheus/assembly/parts.md#anagrapheus.assembly.parts.Register
[merge]: ../reference/anagrapheus/identity/decisions.md#anagrapheus.identity.decisions.merge
[plan-rekeys]: ../reference/anagrapheus/beliefs/rekey.md#anagrapheus.beliefs.rekey.plan_rekeys
[belief-index]: ../reference/anagrapheus/beliefs/index.md#anagrapheus.beliefs.index.BeliefIndex
[replay-pins]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.ReplayPins
[pin-miss]: ../reference/anagrapheus/timeline/replay.md#anagrapheus.timeline.replay.PinMiss
[belief-revised]: ../reference/anagrapheus/beliefs/events.md#anagrapheus.beliefs.events.BeliefRevised
[pinned-model]: ../reference/anagrapheus/provider/model.md#anagrapheus.provider.model.PinnedModel
[pin-ledger]: ../reference/anagrapheus/timeline/pins.md#anagrapheus.timeline.pins.PinLedger
