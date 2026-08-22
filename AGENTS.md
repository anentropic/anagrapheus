# Guidance for AI Agents

This document provides context for AI assistants (like Claude Code) working on this project.

## Project Structure

- **Source**: `src/anagrapheus/` - All package code
- **Tests**: `tests/` - Pytest test suite with fixtures in conftest.py
- **Build**: uv + pyproject.toml, uv_build backend
- **Python**: >=3.11, strict type checking enabled

## Quality Gates (Mandatory)

All code must pass these checks before committing. We use [prek](https://prek.j178.dev/) — a fast, Rust-based runner for `.pre-commit-config.yaml` hooks:

```bash
prek run --all-files  # Runs all hooks (typecheck, lint, format, test)
```

Or run individually:
```bash
uv run basedpyright  # Type checking (strict mode)
uv run ruff check    # Linting
uv run ruff format --check  # Format validation
uv run pytest        # Test suite
```

Apply formatting: `uv run ruff format`

## Code Conventions

- **Line length**: 100 characters maximum
- **Docstrings**: Multi-line docstrings use D213 format (summary on second line after opening quotes)
- **Type annotations**: Strict mode enabled, avoid `# type: ignore`, prefer pyproject.toml-level exemptions
- **Import sorting**: Automated via ruff isort (I rules)

## Development Workflow

1. Make changes to source code
2. Run quality gates (typecheck, lint, format, test)
3. Commit with conventional commit format: `<type>(<scope>): <message>`
   - Types: feat, fix, docs, chore, test, refactor, perf, style
4. CI runs automatically on push (if GitHub Actions enabled)

## Type Checking Notes

- `reportPrivateUsage = false` in basedpyright config - tests may access private members
- Use `from __future__ import annotations` only when necessary for forward references
- Use `TYPE_CHECKING` blocks to avoid circular imports

## Testing Patterns

- Fixtures in `tests/conftest.py` (centralized)
- Test classes group related behaviors
- Test naming: `test_<specific_behavior>`
- Coverage reported but not enforced (visibility only)

## Package Structure

- Public API defined in `src/anagrapheus/__init__.py` via `__all__`
- Version managed in pyproject.toml only (no __version__ in __init__.py)
- Type information available (py.typed marker present)

## Build and Release

- Build: `uv build` (creates wheel + sdist)
- Validate: Test installation in isolated venv before publishing
- Release: Push semver tag (v1.2.3) to trigger automated PyPI publish (if GitHub Actions enabled)

## Common Commands

```bash
# Development setup
uv sync --dev

# Run all quality checks
prek run --all-files

# Run quality checks individually
uv run basedpyright && uv run ruff check && uv run ruff format --check && uv run pytest

# Auto-fix formatting and linting
uv run ruff format && uv run ruff check --fix

# Run tests with coverage
uv run pytest --cov=src/anagrapheus --cov-report=term

# Build package
uv build
```

## Notes for Agents

- Always run quality gates before committing
- Follow conventional commit format for commit messages
- Add new dev dependencies to `[dependency-groups]` dev section in pyproject.toml
- Update __all__ in __init__.py when adding public APIs
- Write tests for new features (prefer TDD when appropriate)
