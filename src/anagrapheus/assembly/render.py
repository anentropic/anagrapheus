"""A plain default renderer. An application's own renderer is expected to replace it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from anagrapheus.assembly.parts import Register

if TYPE_CHECKING:
    from anagrapheus.assembly.parts import Context, Part

__all__ = ["render_labelled"]

_HEADINGS = {
    Register.binding: "MUST HOLD",
    Register.considered: "CONSIDER",
    Register.craft: "CRAFT",
    Register.generated: "SUMMARISED (advisory prose, not canon)",
}


def render_labelled(context: Context) -> str:
    """
    Each register under its own heading, in a fixed order, parts in assembly order.

    Enough for tests and for a first consumer. The wording is not part of any contract:
    NVM's "the *structure* of the brief is fixed here, its *rendering* is not" applies.
    """
    blocks: list[str] = []
    for register in Register:
        lines = [_line(p) for p in context.parts if p.register is register]
        if lines:
            blocks.append(f"{_HEADINGS[register]}:\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _line(part: Part) -> str:
    content = part.content
    return content if isinstance(content, str) else json.dumps(content, sort_keys=True)
