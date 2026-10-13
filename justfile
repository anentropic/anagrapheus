default:
    @just --list

# Set up the agent cli with GSD and skills based on agent name [claude|opencode]
setup-agent-cli agent="claude":
    @selected_agent="{{agent}}"; \
    selected_agent="${selected_agent#agent=}"; \
    case "${selected_agent}" in \
        claude) gsd_name="claude"; skills_name="claude-code" ;; \
        opencode) gsd_name="opencode"; skills_name="opencode" ;; \
        *) echo "Invalid agent '${selected_agent}'. Expected 'claude' or 'opencode'."; exit 1 ;; \
    esac; \
    npx @opengsd/gsd-core --"${gsd_name}" --local; \
    npx skills add abatilo/vimrc/plugins/abatilo-core/skills/diataxis-documentation -a "${skills_name}" -y; \
    npx skills add blader/humanizer -a "${skills_name}" -y

# Build the docs site (strict mode)
docs-build:
    uvx git-cliff -o CHANGELOG.md
    uv run --group docs mkdocs build --strict

# Serve the docs dev server (default port 8000)
docs-serve port="8000":
    uvx git-cliff -o CHANGELOG.md
    uv run --group docs mkdocs serve --dev-addr 127.0.0.1:{{port}} --livereload

# Run the negotiation demo with API keys from .env; pass --model specs, or none for scripted
[positional-arguments]
demo *args:
    uv run --env-file .env python -m examples.negotiation "$@"

# Watch the negotiation in a browser: beliefs, derived models, and every model call
[positional-arguments]
demo-ui *args:
    uv run --env-file .env python -m examples.negotiation.ui "$@"

# Re-record the committed pin corpora after a deliberate prompt change; commit the diff
record-fixtures:
    uv run python -m tests.fixtures.record
