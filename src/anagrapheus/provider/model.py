"""
A model whose every request draws through the pin ledger.

Doc 00 §2 gives the provider *the model call* and forbids it any state that outlives
the call. :class:`PinnedModel` holds to that exactly: it owns no state of its own, and
what persists is the ledger's, which the caller passes in and the caller commits.

The pin attaches **per request**, not per agent run. §4a keys on what consumes the
entropy, and inside one run a tool loop makes several draws — so several keys. Pinning
the run's final answer instead would replay the conclusion while re-running every step
that reached it, which is most of what replay is for.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_core import to_jsonable_python

from anagrapheus.provider.policy import SemanticCallPolicy
from anagrapheus.provider.stream import PinnedStreamedResponse
from anagrapheus.timeline.pins import pin_key

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from contextlib import AbstractAsyncContextManager

    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
    from pydantic_ai.settings import ModelSettings

    from anagrapheus.provider.policy import CallPolicy
    from anagrapheus.timeline._canonical import Json
    from anagrapheus.timeline.pins import PinKey, PinResolver

__all__ = ["PIN_KIND", "PinnedModel"]

#: The pin kind every model request is recorded under.
PIN_KIND = "model_call"

_RESPONSE = TypeAdapter(ModelResponse)

_log = logging.getLogger(__name__)


class PinnedModel(WrapperModel):
    """
    Wraps any pydantic-ai model so each request is pinned, reused and replayable.

    Drops in wherever a model does — ``Agent(model=PinnedModel(inner, resolver))`` or
    ``Agent.override(model=...)`` — so nothing about how an application writes its agent
    changes. Everything except :meth:`request` and :meth:`request_stream` delegates to
    the wrapped model, inherited from ``WrapperModel``.

    ``on_key`` is called with the key of every request this model makes. The key is
    computed internally from the policy, so without this hook a caller wanting to record
    which pin produced a result would have to duplicate the policy to recompute it — and
    a duplicated policy is one that drifts. Used by the chronicle to record a section's
    provenance; ignore it otherwise.

    The resolver decides what pinning *means* here: a
    :class:`~anagrapheus.timeline.pins.PinLedger` draws once and reuses under retry; a
    :class:`~anagrapheus.timeline.replay.ReplayPins` answers from the log and announces
    the first key it cannot. The model does not know which it has, which is the point —
    a unit of work is written once and runs live, under retry, and under replay.
    """

    def __init__(
        self,
        wrapped: Model,
        resolver: PinResolver,
        *,
        policy: CallPolicy | None = None,
        kind: str = PIN_KIND,
        on_key: Callable[[PinKey], None] | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._resolver = resolver
        self._policy = policy if policy is not None else SemanticCallPolicy()
        self._kind = kind
        self._on_key = on_key

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """
        Reuse the pinned response for this call, or make it exactly once.

        ``model_settings`` is deliberately **not** in the key material by default. The
        policy owns that judgment — a temperature change arguably makes a different
        call, and a timeout change plainly does not — and
        :class:`~anagrapheus.provider.policy.SemanticCallPolicy` keeps to what the model
        is *asked*. An application that disagrees supplies its own policy.
        """
        key = self._key(messages, model_request_parameters)
        call = _Call(self, key, messages, model_request_parameters)

        async def draw() -> Json:
            call.drawn = True
            return await self._draw(messages, model_settings, model_request_parameters)

        try:
            response = await self._resolver.resolve_async(
                key, kind=self._kind, effect=draw, interpret=_interpret
            )
        except Exception as error:
            call.failed(error)
            raise
        call.done(response)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """
        Stream the call, pinning what it accumulates to; replay it from the pin after.

        **The key is the same one :meth:`request` would build.** Streaming is how a
        response is *received*, not part of what the model is asked, and §4a keys on the
        semantic identity of the call — so a conversation pins the same whether it was
        streamed or not, and a corpus recorded either way replays either way. That
        property is worth testing in both directions, and is.

        ``run_context`` is excluded for the same reason ``pinned_tool`` excludes a
        ``RunContext`` parameter: it is filled from the run, differs between runs, and
        is not part of what the model was asked. Keying on it would make every replay a
        miss.
        """
        key = self._key(messages, model_request_parameters)
        call = _Call(self, key, messages, model_request_parameters)

        def live() -> AbstractAsyncContextManager[StreamedResponse]:
            call.drawn = True
            return super(PinnedModel, self).request_stream(
                messages, model_settings, model_request_parameters, run_context
            )

        def replayed(raw: Json) -> StreamedResponse:
            return PinnedStreamedResponse(
                model_request_parameters=model_request_parameters,
                pinned=_interpret(raw),
            )

        async with self._resolver.resolve_stream(
            key,
            kind=self._kind,
            effect=live,
            capture=_capture,
            interpret=replayed,
        ) as stream:
            try:
                yield stream
            except Exception as error:
                call.failed(error)
                raise
            call.done(stream.get())

    def _key(self, messages: list[ModelMessage], parameters: ModelRequestParameters) -> PinKey:
        """Build this call's key, and report it to ``on_key`` if one was given."""
        key = pin_key(self._kind, self._policy.material(messages, self.model_id, parameters))
        if self._on_key is not None:
            self._on_key(key)
        return key

    async def _draw(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> Json:
        """Make the real call and reduce it to the JSON the ledger stores."""
        response = await super().request(messages, model_settings, model_request_parameters)
        return cast("Json", _RESPONSE.dump_python(response, mode="json"))


def _capture(stream: StreamedResponse) -> Json:
    """
    Reduce a consumed stream to the JSON the ledger stores.

    ``get()`` builds the accumulated response and stamps it with the state the stream
    ended in, so a stream abandoned or broken part-way pins as the partial it was
    rather than as a complete answer or as nothing at all.
    """
    return cast("Json", _RESPONSE.dump_python(stream.get(), mode="json"))


def _interpret(raw: Json) -> ModelResponse:
    """
    Rebuild a ``ModelResponse`` from what the ledger holds.

    Interpretation runs on **every** resolve — first draw, retry and replay alike — so
    a pinned response is reconstructed by exactly the code that handled the live one,
    and a shape the validator would reject fails at the draw rather than a run later.
    """
    return _RESPONSE.validate_python(raw)


class _Call:
    """
    One request's log line, timed from the key to the answer.

    ``drawn`` is set by the effect itself, which is the only reliable witness: the
    resolver decides whether to draw and does not say which it chose, and a reused pin
    and a fresh draw return the same type. The DEBUG record carries the conversation and
    the answer as ``detail`` -- structured, for a handler that wants to show them -- and
    is built only when something is listening at that level, since it serializes the
    whole conversation.
    """

    def __init__(
        self,
        model: PinnedModel,
        key: PinKey,
        messages: list[ModelMessage],
        parameters: ModelRequestParameters,
    ) -> None:
        self.model = model
        self.key = key
        self.messages = messages
        self.parameters = parameters
        self.drawn = False
        self.started = time.perf_counter()

    def done(self, response: ModelResponse) -> None:
        seconds = time.perf_counter() - self.started
        usage = response.usage
        _log.info(
            "%s %s %s in %.2fs (%d in / %d out tokens) %s",
            self.model.model_id,
            self.model._kind,
            "drawn" if self.drawn else "reused from pin",
            seconds,
            usage.input_tokens,
            usage.output_tokens,
            self.key,
            extra={"detail": self._detail(seconds, response)}
            if _log.isEnabledFor(logging.DEBUG)
            else {},
        )

    def failed(self, error: Exception) -> None:
        _log.warning(
            "%s %s failed after %.2fs: %s",
            self.model.model_id,
            self.model._kind,
            time.perf_counter() - self.started,
            error,
        )

    def _detail(self, seconds: float, response: ModelResponse) -> dict[str, Json]:
        params = self.parameters
        return {
            "model": self.model.model_id,
            "kind": self.model._kind,
            "key": self.key,
            "drawn": self.drawn,
            "seconds": round(seconds, 3),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "tools": [t.name for t in params.function_tools],
            "output_tools": [t.name for t in params.output_tools],
            "request": [_message(m) for m in self.messages],
            "response": _message(response),
        }


#: The part fields worth reading; the rest is bookkeeping (see the call policy).
_SHOWN = ("part_kind", "content", "tool_name", "args")


def _message(message: ModelMessage) -> Json:
    """One message as its parts' readable content, for a log handler to render."""
    parts: list[Json] = []
    for part in message.parts:
        dumped = to_jsonable_python(part, fallback=repr)
        if isinstance(dumped, dict):
            fields = cast("dict[str, Json]", dumped)
            parts.append({k: fields[k] for k in _SHOWN if k in fields})
    return {"kind": message.kind, "parts": parts}
