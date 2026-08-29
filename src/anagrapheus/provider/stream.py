"""
Replaying a pinned response as a stream.

A streamed call is pinned as the :class:`~pydantic_ai.messages.ModelResponse` its
stream accumulated to — not as the sequence of chunks that carried it. So replay has to
rebuild a stream from that response, which is what :class:`PinnedStreamedResponse`
does.

**What replay reproduces, and what it does not.** The parts, the usage, the finish
reason and the response *state* all come back exactly, including the state of a stream
that never finished. What does not come back is the shape of the delivery: chunk
boundaries and inter-chunk timing were the provider's, they are not in the pin, and
inventing plausible ones would be fabricating a stream that never happened. Each part
is therefore surfaced whole, in order.

That is why the design note says a replay *replays a stream* rather than *streams*. The
distinction matters for exactly one kind of consumer — code that acts on chunk
boundaries — and for that consumer the honest answer is that this is not a recording of
its stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai.models import StreamedResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from pydantic_ai.messages import ModelResponse, ModelResponseStreamEvent

__all__ = ["PinnedStreamedResponse"]


@dataclass
class PinnedStreamedResponse(StreamedResponse):
    """A ``StreamedResponse`` whose content comes from the log rather than a provider."""

    pinned: ModelResponse = field(kw_only=True)

    def __post_init__(self) -> None:
        """Restore the accumulated response's own metadata onto the stream."""
        self.provider_response_id = self.pinned.provider_response_id
        self.provider_details = self.pinned.provider_details
        self.finish_reason = self.pinned.finish_reason
        self.metadata = self.pinned.metadata
        self._usage = self.pinned.usage
        # `get()` derives its reported state from these three, so each of the four
        # states a provider can stamp is set by the combination that reproduces it --
        # a stream that was interrupted must replay as interrupted, not as complete.
        state = self.pinned.state
        self.state = state
        self._finished = state == "complete"
        self._cancelled = state == "interrupted"

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        """Surface each pinned part whole, in order."""
        for index, part in enumerate(self.pinned.parts):
            event = self._parts_manager.handle_part(vendor_part_id=index, part=part)
            yield event

    async def close_stream(self) -> None:
        """Nothing to close: there was never a connection."""

    @property
    def model_name(self) -> str:
        return self.pinned.model_name or ""

    @property
    def provider_name(self) -> str | None:
        return self.pinned.provider_name

    @property
    def provider_url(self) -> str | None:
        return self.pinned.provider_url

    @property
    def timestamp(self) -> datetime:
        return self.pinned.timestamp
