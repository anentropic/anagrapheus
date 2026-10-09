"""
A model that plays this negotiation without a provider, and says how it decided.

Doc 00 §2 makes the model call one seam, and this is what that buys: the whole demo —
four rounds, sixteen agent calls and four consolidations, twenty-eight model requests
once the tool loop's continuations are counted — runs in milliseconds with nothing on
the network, and the *same* code path runs against a real provider when ``--live`` swaps
this out.

**It is not a stub and it is not a mock.** It reads the brief it is given and decides
from it, so changing what an agent believes changes what it offers. A stub returning a
fixed offer would let every belief in this example be wrong without a test noticing —
which is the failure mode note 06 keeps finding in its own assertions.

**The three calls are told apart structurally, not by their prose.** Matching on a
phrase in the prompt reads fine and breaks the first time a docstring is reworded; the
output tool's schema is what actually differs, and it differs because the *types* do:
an :class:`~examples.negotiation.agent.Offer` has ``concede``, a
:class:`~examples.negotiation.agent.Reading` has ``predicate``, and the summarizer asks
for no structured output at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.models.function import AgentInfo

__all__ = ["respond", "scripted"]

#: What the brief says when a party has no read of the other at all.
UNKNOWN = "unknown"

_MODELLED = "What we believe they actually want: "
_CONCLUDED = "Our read of their bottom line: "


def scripted(record: Callable[[str], None] | None = None) -> Model:
    """
    The negotiation's scripted counterpart, as an ordinary pydantic-ai model.

    ``record`` is handed every prompt, so a test can count the calls that actually
    reached a model — which is the only way to assert that a replay reached none. It is
    a callback rather than a returned list because the model is built before the run and
    the count is wanted after it.
    """

    def recorded(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if record is not None:
            record(_prompt(messages))
        return respond(messages, info)

    return FunctionModel(recorded)


def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """
    One scripted response, as a plain function.

    Exposed separately from :func:`scripted` so a test can wrap it — override the one
    call it wants to go wrong and let every other call in the round behave normally.
    Reaching into ``FunctionModel.function`` to do that works and types as ``Unknown``,
    which is a way of being told the seam should be a function rather than an attribute.
    """
    prompt = _prompt(messages)
    if not info.output_tools:
        # No structured output: the chronicle's summarizer.
        return ModelResponse(parts=[TextPart(_consolidate(prompt))])
    fields = info.output_tools[0].parameters_json_schema.get("properties", {})
    if "concede" in fields:
        return _offer(messages, info, prompt)
    return _reading(prompt)


def _offer(messages: list[ModelMessage], info: AgentInfo, prompt: str) -> ModelResponse:
    """
    Hold firm unless the other side is believed immovable — and check the archive first.

    The archive call is what makes the tool loop real: the first response is a tool
    call, the tool answers, and the second response is the offer. A replay has to
    reproduce **both**, and pinning only the model half would silently re-run the
    lookup.
    """
    if any(name == "precedent" for name in _tools_offered(info)) and not _already_asked(messages):
        return ModelResponse(parts=[ToolCallPart("precedent", {"clause": _clause(prompt)})])
    theirs = _line(prompt, _MODELLED)
    concluded = _line(prompt, _CONCLUDED)
    return ModelResponse(
        parts=[
            ToolCallPart(
                "final_result",
                {
                    "clause": _clause(prompt),
                    # Concede only against a side believed immovable -- by what we derive
                    # they want, or by what we concluded about them last round. An
                    # opening round, where both are empty, holds firm: `unknown` is not
                    # `never`, and treating it as one would make ignorance concede.
                    "concede": "never" in (theirs, concluded),
                    "because": f"we read their position as {theirs}",
                },
            )
        ]
    )


def _reading(prompt: str) -> ModelResponse:
    """
    Conclude one thing about the other side's bottom line.

    Always a ``red_line``: a disposition, held with no witness trail, which is exactly
    R28's exception and the one belief about another party that may legitimately be
    stored. What they *believe* stays derived and unwritten.
    """
    theirs = _line(prompt, _MODELLED)
    return ModelResponse(
        parts=[
            ToolCallPart(
                "final_result",
                {
                    "about": _other(prompt),
                    "clause": _clause(prompt),
                    "predicate": "red_line",
                    "value": theirs,
                    "because": "what the round showed of their position",
                },
            )
        ]
    )


def _consolidate(prompt: str) -> str:
    """One sentence, deterministic, from the round the fold handed over."""
    return prompt.split(": ", 1)[-1].strip() or "nothing happened"


def _prompt(messages: list[ModelMessage]) -> str:
    """The user text of this request, ignoring tool returns."""
    for message in reversed(messages):
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and not isinstance(part, ToolReturnPart):
                return content
    return ""


def _tools_offered(info: AgentInfo) -> tuple[str, ...]:
    return tuple(tool.name for tool in info.function_tools)


def _already_asked(messages: list[ModelMessage]) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == "precedent"
        for message in messages
        for part in message.parts
    )


def _line(prompt: str, prefix: str) -> str:
    """One labelled line of the brief, or ``unknown``."""
    for line in prompt.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return UNKNOWN


def _clause(prompt: str) -> str:
    return _line(prompt, "Clause: ")


def _other(prompt: str) -> str:
    """
    Who the brief is about, taken from the sentence that names them.

    The update call ends with "Record one conclusion about what X actually wants", and
    X is the only place in the prompt where the other party is named unambiguously —
    the belief lines say "they".
    """
    marker = "Record one conclusion about what "
    for line in prompt.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].split(" ", 1)[0]
    return UNKNOWN
