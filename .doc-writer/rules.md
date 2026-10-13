# Project Documentation Rules

These rules are specific to this project and extend the universal quality rules
enforced by all doc-writer agents. Edit this file to add project-specific
documentation guidelines.

## How Rules Work

- Universal rules (no internal details, working examples, consistent structure,
  cross-references, persona calibration) are always enforced and cannot be
  overridden.
- Rules in this file are additive — they add project-specific requirements on
  top of the universal baseline.
- Write rules as natural language instructions. Agents interpret them directly.
- Be specific. "Use clear language" is too vague. "Always include a Requirements
  section in how-to guides listing minimum dependency versions" is actionable.

## Project Rules

<!-- Add your project-specific documentation rules below. Examples: -->

<!-- - Always include a "Requirements" section in how-to guides listing minimum versions -->
<!-- - Use American English spelling throughout -->
<!-- - Code examples must show error handling, not just the happy path -->
<!-- - Reference the changelog entry when documenting breaking changes -->
<!-- - Include performance characteristics in reference pages for I/O operations -->
<!-- - Prefer async examples over sync when both are available -->

- Use British English spelling (behaviour, summarise, recognise), matching the code and design notes.
- Every code example must run against the current public API: import from `anagrapheus` or its documented subpackages, never from a private module (one whose name starts with `_`).
- Do not cite the internal design notes (`_design/`), R-numbers or milestone names (M1–M9) as if the reader knows them; explain the idea itself.
- Prefer the negotiation example (`examples/negotiation/`) for worked scenarios, since it exercises every layer and its recordings replay in CI.
- Say plainly which decisions belong to the application and which to the library (call policy, predicate vocabularies, scope lists, re-key loops); the library deliberately leaves several to the application.
