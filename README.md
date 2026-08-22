# anagrapheus

Event-sourced timeline, belief store, and identity layer for long-running agents.

> **Status: scaffolding.** Nothing is implemented yet, and the package is not
> published. This repository is the implementation of the **agent-core** stack
> designed in
> [`narrative-vm/_design/agent-core/`](https://github.com/anentropic/narrative-vm/tree/main/_design/agent-core)
> — the event-sourced timeline (*archeion*), the belief store (*doxastica*),
> the identity layer, and the seams between them.

## Installation

```bash
pip install anagrapheus
```

## Development

### Quality Gates

Before committing, all code must pass:

```bash
prek run --all-files  # Runs typecheck, lint, format, test
```

Or run individually:
```bash
uv run basedpyright  # Type checking (strict mode)
uv run ruff check    # Linting
uv run ruff format --check  # Formatting
uv run pytest        # Tests
```

### Setup

```bash
# Install dependencies
uv sync --dev

# Install git hooks
prek install
```

## License

BSD-3-Clause
