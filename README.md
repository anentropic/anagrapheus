# anagrapheus

[![CI](https://github.com/anentropic/anagrapheus/actions/workflows/ci.yml/badge.svg)](https://github.com/anentropic/anagrapheus/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/anagrapheus)](https://pypi.org/project/anagrapheus/)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue)](https://github.com/anentropic/anagrapheus/blob/main/LICENSE)

An event-sourced timeline, belief store and identity layer for long-running LLM agents. One append-only event log is the authoritative record of a run; per-agent beliefs, entity identity, a consolidated history and assembled prompts are all projections of it that can be dropped and rebuilt. Every model call and tool call is pinned to the log, so a recorded run replays offline, exactly, without reaching the model, and a run whose prompts have changed reports where it diverged instead of quietly drawing new answers.

It is for Python developers building agents and simulations that run for many turns and hold private beliefs about each other: negotiations, games, interactive fiction. It builds on [pydantic-ai](https://ai.pydantic.dev/) for the model call and works with any provider pydantic-ai supports.

**[Documentation](https://anentropic.github.io/anagrapheus/)**

## Quick Start

### Installation

anagrapheus needs Python 3.14 or later.

```bash
pip install anagrapheus
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add anagrapheus
```

The base install runs offline. To call a real provider, add its extra: `anagrapheus[anthropic]` or `anagrapheus[openai]` (the OpenAI SDK, also used for OpenAI-compatible routes such as OpenRouter).

### First use

Record one model call, then replay it from the log. This runs offline on pydantic-ai's `FunctionModel`:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus import register_builtin_events
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import EventTypeRegistry, PinLedger, ReplayPins, SQLiteEventStore

calls = 0


def delegate(messages, info):
    global calls
    calls += 1
    return ModelResponse(parts=[TextPart("The EU proposes a 60/40 split.")])


types = EventTypeRegistry()
register_builtin_events(types)
store = SQLiteEventStore("summit.db")

# Record: each model request is drawn once and pinned, then committed to the log.
ledger = PinLedger(store, types)
agent = Agent(PinnedModel(FunctionModel(delegate), ledger))
print(agent.run_sync("Open the fisheries talks.").output)
ledger.commit()

# Replay: every request is answered from the log, so the model is never called.
replay = ReplayPins.from_events(store.read(), types)
agent = Agent(PinnedModel(FunctionModel(delegate), replay))
print(agent.run_sync("Open the fisheries talks.").output)
print(f"model calls: {calls}")
```

```text
The EU proposes a 60/40 split.
The EU proposes a 60/40 split.
model calls: 1
```

The [Getting Started tutorial](https://anentropic.github.io/anagrapheus/tutorials/getting-started/) extends this to two agents, a recording file replayed in a separate process, and a deliberate pin miss.

## Key Features

- **Pinned model and tool calls**: `PinnedModel` wraps any pydantic-ai model and `pinned_tool` wraps any tool, so each request is keyed on what was asked, drawn once, reused on retry and answered from the log on replay. Streaming responses are pinned as their accumulated result.
- **Replay that says when it is not a replay**: strict replay raises `PinMiss` at the first request it has no pin for, before the model is called; record mode marks the divergence and continues with fresh draws.
- **Recordings in the log's own format**: `write_recording` and `read_recording` store a run's pins as JSON lines with a header naming what produced them, and old recordings can be upcast on read like any other event.
- **The application decides what "the same call" means**: the default `SemanticCallPolicy` ignores timestamps and provider ids; a custom `CallPolicy` can change what goes into the key.
- **Per-agent beliefs as a fold**: beliefs are held per scope (one per holder) in [doxastica](https://pypi.org/project/doxastica/), with ids minted only from predicates declared in code, so two agents can disagree and `divergence` measures where.
- **Identity decisions as events**: establish, alias, merge and split are appended to the log; a merge produces re-key rows, and `plan_rekeys` says which beliefs must move.
- **A chronicle for long histories**: older ranges of the log are consolidated into sections that cite the range they cover, read alongside the recent raw events.
- **Prompts from granted slices**: `assemble` builds a context only from the state a role was granted, rejects any part citing a source the grant never handed out, and fits a budget that never drops a binding part.
- **Event schemas that can change**: register upcasters and old logs keep reading at the current schema.

## Tech Stack

Python 3.14+, [pydantic](https://docs.pydantic.dev/) and [pydantic-ai](https://ai.pydantic.dev/) for events and the model seam, [doxastica](https://pypi.org/project/doxastica/) for the belief store, and the standard library's `sqlite3` behind store protocols. One SQLite file can hold every component.

A complete worked example, a four-round EU and UK negotiation that uses every layer, lives in [`examples/negotiation/`](examples/negotiation/). From a clone, run it offline with `uv run python -m examples.negotiation`.

## Development

See [DEVELOP.md](DEVELOP.md) for the full guide.

### Setup

```bash
# Install dependencies (the dev group includes the anthropic and openai extras)
uv sync --dev

# Install git hooks
prek install
```

### Quality gates

Before committing, run the hooks:

```bash
prek run --all-files  # file hygiene, ruff lint and format, uv lock, basedpyright, blacken-docs
```

The hooks do not run the tests. Run each gate individually with:

```bash
uv run basedpyright         # Type checking (strict mode)
uv run ruff check           # Linting
uv run ruff format --check  # Formatting
uv run pytest               # Tests
```

`uv run pytest` skips the live smoke tests, which reach a real provider. Opt in with `uv run pytest -m tier3`, which needs an API key for at least one of the routes in `tests/tier3`.

### Docs

```bash
uv sync --dev --group docs
just docs-serve  # or: just docs-build
```

Both recipes regenerate `CHANGELOG.md` with git-cliff before building.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
