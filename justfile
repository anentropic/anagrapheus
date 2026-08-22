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
    npx get-shit-done-cc --"${gsd_name}" --local; \
    npx skills add abatilo/vimrc/plugins/abatilo-core/skills/diataxis-documentation -a "${skills_name}" -y; \
    npx skills add blader/humanizer -a "${skills_name}" -y

# Build the docs site (strict mode)
docs-build:
    uvx git-cliff -o CHANGELOG.md
    uv run mkdocs build --strict

# Serve the docs dev server (default port 8000)
docs-serve port="8000":
    uvx git-cliff -o CHANGELOG.md
    uv run mkdocs serve --dev-addr 127.0.0.1:{{port}} --livereload
