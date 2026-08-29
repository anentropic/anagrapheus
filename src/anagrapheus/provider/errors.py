"""Provider error types."""

from __future__ import annotations

__all__ = ["ProviderError", "StreamingNotPinnable"]


class ProviderError(Exception):
    """Base class for all provider errors."""


class StreamingNotPinnable(ProviderError):
    """
    A pinned model was asked to stream, which it cannot record as one result.

    Raised rather than delegating to the wrapped model, which is what the inherited
    behaviour would do. Passing a stream through would put an **unpinned live draw**
    inside a run that believes every draw is pinned: the run would not replay, and
    nothing would say so — precisely the silence pinning exists to remove.

    Use the non-streaming path for anything whose result must be reproducible, and reach
    for the unwrapped model where a stream is genuinely wanted and its irreproducibility
    is understood.
    """
