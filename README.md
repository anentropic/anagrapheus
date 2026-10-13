# anagrapheus

[![CI](https://github.com/anentropic/anagrapheus/actions/workflows/ci.yml/badge.svg)](https://github.com/anentropic/anagrapheus/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/anagrapheus)](https://pypi.org/project/anagrapheus/)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-blue)](https://github.com/anentropic/anagrapheus/blob/main/LICENSE)

An event-sourced timeline, belief store and identity layer for long-running LLM agents. One append-only event log is the authoritative record of a run; per-agent beliefs, entity identity, a consolidated history and assembled prompts are all projections of it that can be dropped and rebuilt. Every model call and tool call is pinned to the log, so a recorded run replays offline, exactly, without reaching the model, and a run whose prompts have changed reports where it diverged instead of quietly drawing new answers.

It is for Python developers building agents and simulations that run for many turns and hold private beliefs about each other: negotiations, games, interactive fiction. It builds on [pydantic-ai](https://ai.pydantic.dev/) for the model call and works with any provider pydantic-ai supports.

## Installation

anagrapheus needs Python 3.14 or later.

```bash
pip install anagrapheus  # or: uv add anagrapheus
```

The base install runs offline. To call a real provider, add its extra: `anagrapheus[anthropic]` or `anagrapheus[openai]` (the OpenAI SDK, also used for OpenAI-compatible routes such as OpenRouter).

## Documentation

**[anentropic.github.io/anagrapheus](https://anentropic.github.io/anagrapheus/)** has a getting-started tutorial, how-to guides for each layer, explanations of the design, and the API reference.

A worked example, a four-round EU and UK negotiation that uses every layer, lives in [`examples/negotiation/`](examples/negotiation/). From a clone, run it offline with `uv run python -m examples.negotiation`.

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
