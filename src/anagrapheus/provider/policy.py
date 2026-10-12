"""
What counts as "the same model call".

This is the whole design, and doc 00 §4a is unusually specific about it: **hash only
what consumes the entropy** — for a model call, *"the semantic identity of the call,
never the serialized request shape and never how you intend to parse the response."*
Both naive options fail. Key on the entire request and re-framing a decision re-rolls
it; key on the draw alone while letting the caller see the raw and a threshold can be
fitted to a result already seen.

§4c then says the canonicalization policy is **the application's** — the library owns
the mechanism, the domain owns what "same" means. So :class:`CallPolicy` is a Protocol
with a working default, the same arrangement identity's vocabulary and beliefs'
predicate registry both take.

**What the default excludes, and why it must.** pydantic-ai's message parts carry
`timestamp` on every user, system and tool-return part; `id`, `provider_name` and
`provider_details` on text and tool-call parts; and a provider-assigned `tool_call_id`
pairing a call with its return. None of those is part of what the model is being asked.
Leaving them in would give every call a fresh key — pinning would appear to work,
reuse would never happen, and the failure would be silent and expensive.

Dropping `tool_call_id` is the one that deserves a note: it *does* carry information —
which return answers which call — but that pairing is already implied by the order of
the parts, and the id itself is assigned by the provider and differs between two runs
of the same conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import ModelRequestParameters

    from anagrapheus.timeline._canonical import Json

__all__ = ["CallPolicy", "SemanticCallPolicy"]

#: Part fields that vary between two calls that are the same call.
_VOLATILE = frozenset(
    {"timestamp", "id", "tool_call_id", "provider_name", "provider_details", "metadata"}
)

#: Request parameters about how the call is delivered, not what the model is asked.
#: ``cache`` is prompt-caching configuration: the same prompt drawn with or without a
#: cache hit is the same draw, the same reason ``model_settings`` stays out of the key.
_DELIVERY = frozenset({"cache"})


class CallPolicy(Protocol):
    """
    Reduces a model call to the material its pin key is built from.

    Implement this to say what your domain means by "the same call". A policy that
    includes too much makes every call a fresh draw and pinning does nothing; one that
    includes too little makes two genuinely different calls share an answer. Neither
    failure announces itself, which is why the decision is explicit rather than
    inferred.
    """

    def material(
        self,
        messages: Sequence[ModelMessage],
        model_id: str,
        parameters: ModelRequestParameters,
    ) -> Mapping[str, Json]:
        """The key material for this call — structured data, never prose."""
        ...


class SemanticCallPolicy:
    """
    The default: what the model is being asked, and nothing about the asking.

    Keeps the conversation's semantic content, the model's identity, and the shape the
    answer is required to take — because each of those changes what is drawn. Drops
    everything in :data:`_VOLATILE`, which changes between two runs of an identical
    call.

    The **output shape is included** deliberately, and it is the one inclusion that
    looks like it might violate §4a's "never how you intend to parse the response".
    It does not: a required output schema is sent *to* the model and changes what it
    generates, so it consumes entropy. What §4a forbids is keying on the parsing you
    will do *after* the draw, which is what ``interpret`` handles and what never
    reaches this material.
    """

    def material(
        self,
        messages: Sequence[ModelMessage],
        model_id: str,
        parameters: ModelRequestParameters,
    ) -> Mapping[str, Json]:
        """The key material for this call — structured data, never prose."""
        return {
            "model": model_id,
            "messages": [self._message(message) for message in messages],
            "parameters": self._parameters(parameters),
        }

    def _message(self, message: ModelMessage) -> Json:
        """One message reduced to its parts' semantic content."""
        return {
            "kind": message.kind,
            "parts": [self._part(part) for part in message.parts],
        }

    def _part(self, part: object) -> Json:
        """
        One part with its volatile fields removed.

        Reducing by *exclusion* rather than by listing the fields of each known part
        type: pydantic-ai adds part kinds between releases, and a policy that
        allow-listed them would silently drop a new one's content from the key —
        making two different calls collide, which is the worse of the two failures.
        """
        dumped = cast("Json", to_jsonable_python(part))
        if not isinstance(dumped, dict):
            return dumped
        return {key: value for key, value in dumped.items() if key not in _VOLATILE}

    def _parameters(self, parameters: ModelRequestParameters) -> Json:
        """
        Everything the model is told it may do and must produce.

        Reduced by **exclusion**, like the message parts and for the stronger version of
        the same reason. An earlier draft named the fields it thought mattered — the
        tool names and the output mode — and got it wrong twice over: a tool that kept
        its name while changing its description or argument schema shared a key with the
        old contract, and whole fields (native tools, tool visibility, the instructions)
        were missing outright. Both failures replay an answer produced under different
        terms, and neither says anything.

        Tool lists are sorted by name, because declaring the same capabilities in
        another order is the same capability set and the model is not told the order.
        The fields in :data:`_DELIVERY` are dropped: they change how the request travels,
        not what is drawn.
        """
        dumped = cast("Json", to_jsonable_python(parameters, fallback=repr))
        if not isinstance(dumped, dict):
            return dumped
        return {
            key: self._ordered(key, value) for key, value in dumped.items() if key not in _DELIVERY
        }

    @staticmethod
    def _ordered(field: str, value: Json) -> Json:
        """Sort a list of tool definitions by name; leave everything else alone."""
        if not field.endswith("tools") or not isinstance(value, list):
            return value
        if not all(isinstance(item, dict) and "name" in item for item in value):
            return value
        return sorted(value, key=lambda item: str(cast("dict[str, Json]", item)["name"]))
