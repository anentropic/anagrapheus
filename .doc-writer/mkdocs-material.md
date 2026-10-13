# MkDocs Material Guidance

Opinionated preferences for writing documentation with MkDocs Material. These are style preferences — agents already understand MkDocs Material syntax. This file guides how features should be used, not what they are.

## Admonitions

- Prefer admonition blocks over bold text for callouts. Bold text in paragraphs gets lost; admonitions create visual hierarchy that readers scan.
- Use specific admonition types instead of generic `!!! note`:
  - `!!! tip` for best practices and recommended approaches
  - `!!! warning` for gotchas, common mistakes, and things that might surprise
  - `!!! danger` for breaking changes, security concerns, and data loss risks
  - `!!! info` for supplementary context that enriches understanding
  - `!!! example` for extended examples that would disrupt the flow if inline
- Use collapsible admonitions (`??? note "Title"`) for optional or advanced details that most readers can skip. This keeps the main content focused.
- Do not stack multiple admonitions back-to-back. If you need three warnings in a row, consolidate into one admonition with a list.

## Code Blocks

- Always specify the programming language on fenced code blocks. Never use bare triple backticks.
- Use line highlighting (`hl_lines="3 4"`) to draw attention to the important lines in longer examples. Do not highlight everything — highlight only the lines that are new or critical.
- Use code annotations for complex examples where inline comments would be too noisy. Annotations provide explanation without cluttering the code.
- Prefer tabbed multi-language examples (using content tabs) over separate consecutive code blocks when showing the same concept in multiple languages.
- Use the `title` attribute on code blocks to label file paths: `` ```python title="src/main.py" `` `.

## Content Tabs

- Use content tabs when showing the same concept in multiple languages, frameworks, or installation methods. Tabs keep alternatives side-by-side without page bloat.
- Keep tab labels short and consistent: "Python", "JavaScript", "Rust" — not "Python Example Code" or "Using Python".
- Group related alternatives: install methods (pip/conda/docker), OS-specific instructions (macOS/Linux/Windows), sync/async patterns.
- Do not use tabs for unrelated content. If the tabs show different concepts rather than different expressions of the same concept, use separate sections instead.

## Navigation

### Three Navigation Strategies

The setup Q&A offers three strategies. The guidance below documents how each renders in MkDocs Material so the doc-author writes the correct structure.

- The front page (index.md) must appear as "Overview" in the nav — at the same level as the diataxis section tabs or sidebar sections. Use "Overview" rather than "Home" — it signals that the page orients the reader to the project and its documentation structure.
- For sidebar-only nav (no tabs), "Overview" is the first entry in the left sidebar at the same level as the section headings.

#### Strategy 1: Sections as Top Tabs

Each diataxis section (Tutorials, How-To Guides, Explanation, Reference) becomes a top tab. The left sidebar shows only the active section's pages.

- Requires `navigation.tabs` and `navigation.tabs.sticky` in theme features.
- Requires `navigation.indexes` to make section index pages clickable (the section title links to the index page rather than just expanding the section).
- Tabs are derived from the top-level entries in the `nav:` section of mkdocs.yml. Each top-level key becomes a tab.
- The sidebar automatically shows only the active tab's children -- no custom JavaScript needed.
- MkDocs-Material tabs are flat. They do NOT support dropdown menus or children with summaries (unlike Sphinx/Shibuya nav_links).
- Every tab section must have at least 2 child pages. A tab with fewer than 2 children renders a nearly-empty sidebar.

Example mkdocs.yml for sections-as-tabs:

```yaml
theme:
  features:
    - navigation.tabs
    - navigation.tabs.sticky
    - navigation.indexes
    - navigation.sections

nav:
  - Home: index.md
  - Tutorials:
    - tutorials/index.md
    - Getting Started: tutorials/getting-started.md
    - Build Your First App: tutorials/first-app.md
  - How-To Guides:
    - how-to/index.md
    - Authentication: how-to/authentication.md
  - Explanation:
    - explanation/index.md
    - Architecture Overview: explanation/architecture.md
  - Reference:
    - reference/index.md
```

#### Strategy 2: Single "Docs" Tab

One "Docs" tab containing all documentation sections. Other tabs are reserved for non-docs content (blog, changelog, etc.).

- Same feature flags as sections-as-tabs (`navigation.tabs`, `navigation.tabs.sticky`, `navigation.indexes`).
- All diataxis sections are nested under a single "Docs" top-level key.
- The sidebar shows the full docs tree under the Docs tab.
- The "Docs" tab section must have at least 2 child sections (this is satisfied by having multiple diataxis sections inside it).

Example mkdocs.yml for single Docs tab:

```yaml
theme:
  features:
    - navigation.tabs
    - navigation.tabs.sticky
    - navigation.indexes

nav:
  - Home: index.md
  - Docs:
    - docs/index.md
    - Tutorials:
      - tutorials/index.md
      - Getting Started: tutorials/getting-started.md
    - How-To Guides:
      - how-to/index.md
      - Authentication: how-to/authentication.md
    - Explanation:
      - explanation/index.md
      - Architecture Overview: explanation/architecture.md
    - Reference:
      - reference/index.md
```

#### Strategy 3: Sidebar Only

All navigation in the left sidebar, no top tabs. Sections are rendered as expandable groups.

- No `navigation.tabs` or `navigation.tabs.sticky` in theme features.
- `navigation.sections` renders section headers as bold labels in the sidebar.
- Best for projects with fewer than 6 pages where tabs add overhead.
- `navigation.indexes` can still be enabled to make section index pages clickable in the sidebar.

### Left Nav Depth

- Keep left nav depth to 3 levels maximum (section heading -> page -> sub-page). Deeper nesting makes content hard to find and creates narrow, scrolling nav panels.
- Level 1: section heading (e.g., "Authentication"). Level 2: page link (e.g., "OAuth setup"). Level 3: child page link as a submenu entry. Avoid Level 4.
- Group related pages under a single section. Prefer fewer sections with more pages over many sections with 1-2 pages each.
- Use `index.md` pages for section landing pages. These should provide an overview of what the section contains and guide readers to the right page.

### Section Index Pages with Abstracts

Section index pages (e.g., `tutorials/index.md`) serve as landing pages for each documentation section. They list child pages with a 1-sentence abstract beneath each link.

- Each child page should have `description` in its frontmatter. This provides metadata for HTML meta tags, social cards, and search results.
- The section index page writes abstracts INLINE beneath each link -- readers see the abstracts directly on the index page. MkDocs-Material does NOT auto-render child page descriptions on the parent index.
- Enabled via `navigation.indexes` feature flag, which makes the section title in the sidebar link to the index page.
- Use a styled list with bold links and description text for visual presentation.
- Group links by sub-section if the section has 6+ children.

Example section index page (Markdown) -- with YAML frontmatter (title + description fields) followed by the page body:

```yaml
# frontmatter for tutorials/index.md
title: Tutorials
description: Step-by-step learning guides for new users
```

Page body (after frontmatter):

    # Tutorials

    Learn to use the library from scratch with these step-by-step guides.

    - **[Getting Started](getting-started.md)** -- Install the library and run your first example in under 5 minutes.
    - **[Build Your First App](first-app.md)** -- Walk through building a complete application from an empty directory to a working deployment.

Example child page frontmatter:

```yaml
# frontmatter for tutorials/getting-started.md
title: Getting Started
description: Install the library and run your first example in under 5 minutes
```

See 15-RESEARCH.md "Section Index Page with Abstracts (MkDocs-Material)" for the complete file format with frontmatter delimiters.

## Frontmatter

- Every page needs `title` and `description` fields. The title appears in the browser tab and search results; the description appears in search snippets and link previews.
- Tutorials should include a `difficulty` field (beginner, intermediate, advanced) to help readers self-select appropriate content.
- Use `tags` for cross-cutting concerns that span sections (e.g., `tags: [authentication, security]`).

## Cross-References

- Use reference-style links `[text][reference]` for internal links that appear in multiple places. Define references at the bottom of the page.
- Inline code mentions of functions, classes, and config keys should link to their API Reference page: `` [`connect()`](../reference/client.md#connect) ``.
- Prefer relative paths for internal links. Absolute paths break when the site root changes.

## Mermaid Diagrams

MkDocs Material renders mermaid diagrams natively through the pymdownx.superfences custom fence already configured in the scaffold. No additional plugins or configuration needed.

- Use standard fenced code blocks with the `mermaid` language identifier:

  ````markdown
  ```mermaid
  sequenceDiagram
      participant User
      participant API
      User->>API: POST /items
      API-->>User: 201 Created
  ```
  ````

- Mermaid diagrams render in both light and dark mode automatically via Material's built-in theme integration.
- Do NOT add `mkdocs-mermaid2-plugin` -- it is redundant with the pymdownx.superfences custom fence and can cause conflicts.
- Diagram source is written inline in the Markdown file. No external files or build steps required.
