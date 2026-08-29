"""
Pinning a tool call, which is a nondeterministic effect like any other.

Doc 00 §4 lists the effects worth pinning as *"a model call, an RNG draw, a tool
invocation, an external fetch"* — the last two are tools. Pinning only the model side
of a run gives a replay that reproduces what was *said* while re-running what was
*done*: the fetch repeats, the row is written twice, the payment is made again. That is
not a replay, and it fails silently, because the model half looks perfect.

**Which tools to pin is the caller's judgment, and it matters in both directions.** A
tool that reads the world (a search, a fetch, a query) wants pinning: replay should see
what the run saw, not what is true now. A tool that is pure needs none — it recomputes
identically for free. And a tool that is *deliberately* live — a clock, a
"what is the current price" read whose whole point is freshness — must **not** be
pinned, because a replay is supposed to show the old answer only where the old answer
is what the run acted on.

**Why the wrapper is always async.** pydantic-ai runs a *sync* tool in a worker thread,
and the pin ledger's SQLite connection belongs to the thread that opened it — so a sync
wrapper would reach the store from the wrong thread and fail. Rather than loosening the
store's threading contract for everyone (which would turn concurrent tool calls into a
data race on one connection), the wrapper is a coroutine: the ledger is touched on the
caller's thread, and a sync tool body is offloaded so a slow one still does not block
the loop. The split is exactly what
:meth:`~anagrapheus.timeline.pins.PinResolver.resolve_async` exists to allow.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any, cast, get_origin, get_type_hints

from anyio.to_thread import run_sync
from pydantic_ai import RunContext
from pydantic_core import to_jsonable_python

from anagrapheus.timeline.pins import pin_key

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    from anagrapheus.timeline._canonical import Json
    from anagrapheus.timeline.pins import PinResolver

__all__ = ["PIN_KIND", "pinned_tool"]

#: The pin kind every tool invocation is recorded under.
PIN_KIND = "tool_call"


def _injected_parameter(signature: inspect.Signature, func: Callable[..., object]) -> str | None:
    """
    The name of the ``RunContext`` parameter, if the tool takes one.

    pydantic-ai injects the context by the **first parameter's annotation**, not by its
    name, and matching on a name list gets it wrong in both directions: a context called
    ``state`` would leak per-run state into the key so every replay misses, and an
    ordinary model argument called ``context`` would be dropped so two different calls
    collide. The second is the worse one — it answers, wrongly.

    Annotations may still be strings when the defining module uses postponed evaluation,
    so the resolved hints are preferred and the raw annotation is the fallback.
    """
    first = next(iter(signature.parameters.values()), None)
    if first is None:
        return None
    annotation = _resolved(func).get(first.name, first.annotation)
    if annotation is RunContext or get_origin(annotation) is RunContext:
        return first.name
    # A string annotation that never resolved -- an unimportable name in the module, say.
    # Matching textually is a guess, but the alternative is treating a context as a model
    # argument, which puts run state in the key.
    return first.name if str(annotation).lstrip("'\"").startswith("RunContext") else None


def _resolved(func: Callable[..., object]) -> Mapping[str, object]:
    """The function's type hints, or nothing if they cannot be resolved."""
    try:
        return get_type_hints(func)
    except Exception:  # noqa: BLE001 -- an unresolvable hint is not this layer's problem
        return {}


def pinned_tool[**P, R](
    resolver: PinResolver,
    func: Callable[P, R],
    *,
    name: str | None = None,
    kind: str = PIN_KIND,
) -> Callable[P, Coroutine[Any, Any, R]]:
    """
    Wrap a tool so its result draws through ``resolver`` once and replays thereafter.

    Returns a coroutine function whichever kind it was given — see the module
    docstring for why — with the wrapped function's signature intact, so pydantic-ai
    builds the same tool schema from it and the model sees no difference.

    The key is the tool's name and its arguments: structured data rather than a rendered
    call, the same discipline as everywhere else here. A ``RunContext`` parameter is
    excluded, because it is filled from the run rather than by the model, differs
    between runs, and is not part of what the tool was asked.

    **The result must be JSON-shaped**, because it is what gets recorded. A tool
    returning something unserializable fails at the draw rather than at commit, which is
    the right time — a pin that cannot be stored is a pin that would not replay, and the
    run finds out while it can still do something about it.
    """
    tool_name = name if name is not None else func.__name__
    signature = inspect.signature(func)
    injected = _injected_parameter(signature, func)

    def material(args: tuple[object, ...], kwargs: Mapping[str, object]) -> Mapping[str, Json]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return {
            "tool": tool_name,
            "arguments": cast(
                "Json",
                to_jsonable_python(
                    {
                        parameter: value
                        for parameter, value in bound.arguments.items()
                        if parameter != injected
                    },
                    fallback=repr,
                ),
            ),
        }

    @functools.wraps(func)
    async def pinned(*args: P.args, **kwargs: P.kwargs) -> Any:  # noqa: ANN401
        async def draw() -> Any:  # noqa: ANN401
            if inspect.iscoroutinefunction(func):
                return await cast("Any", func(*args, **kwargs))
            # Offloaded so a slow tool does not block the loop, exactly as pydantic-ai
            # would have run it -- while the ledger stays on the calling thread.
            result = await run_sync(functools.partial(func, *args, **kwargs))
            # A plain callable is allowed to return an awaitable -- pydantic-ai supports
            # that shape -- and `iscoroutinefunction` is false for it. Recording the
            # coroutine object instead of awaiting it would change what wrapping a tool
            # does, which is the one thing a wrapper must not do.
            return await result if inspect.isawaitable(result) else result

        return await resolver.resolve_async(
            pin_key(kind, material(args, kwargs)),
            kind=kind,
            effect=draw,
            interpret=lambda raw: raw,
        )

    return cast("Callable[P, Coroutine[Any, Any, R]]", pinned)
