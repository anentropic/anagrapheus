"""
The model call, pinned — so a run can be replayed instead of merely repeated.

This layer owns *the model call: structured outputs, tool loop, casting*, and is
forbidden **any state that outlives the call**. That constraint is what makes the rest
of the stack testable without a model at all: the model-touching surface is one seam
rather than a property smeared across the codebase.

What this subpackage adds to pydantic-ai is one thing — every nondeterministic effect
draws through the pin ledger:

- :class:`PinnedModel` wraps any model so each request is keyed, drawn once, and reused
  on retry or answered from the log on replay. Per **request**, not per run: a tool loop
  makes several draws, and a pin keys on what consumes the entropy.
- :func:`pinned_tool` does the same for a tool invocation, which is as much a
  nondeterministic effect as the model call. Without it a replay reproduces what the
  agent *said* while re-running what it *did*.
- :class:`CallPolicy` decides what counts as the same call. That is the application's
  judgment; :class:`SemanticCallPolicy` is a default that keeps what the model is asked
  and drops the timestamps and provider ids that would otherwise give every call a fresh
  key.

Streaming is pinned like any other draw: the stream's accumulated response is what
gets recorded, and replay rebuilds a stream from it — see
:class:`~anagrapheus.provider.stream.PinnedStreamedResponse` for what that does and
does not reproduce.
"""

# Design: note 04; upstream agent-core doc 00 §2, §4, §4a, §4c.

from anagrapheus.provider.errors import ProviderError
from anagrapheus.provider.model import PIN_KIND, PinnedModel
from anagrapheus.provider.policy import CallPolicy, SemanticCallPolicy
from anagrapheus.provider.stream import PinnedStreamedResponse
from anagrapheus.provider.tools import PIN_KIND as TOOL_PIN_KIND
from anagrapheus.provider.tools import pinned_tool

__all__ = [
    "PIN_KIND",
    "TOOL_PIN_KIND",
    "CallPolicy",
    "PinnedModel",
    "PinnedStreamedResponse",
    "ProviderError",
    "SemanticCallPolicy",
    "pinned_tool",
]
