"""
Tier 3: does this still work against an actual provider — on every route with a key.

The test plan had four tiers and this is the one that took six milestones to build. It
was asked whether it gates a release, and the objection answered itself: *"a nightly that
nobody reads is not a smoke test."* **Decided: it gates a release.** It runs on demand
and on a release tag, never on a pull request — fork PRs cannot see
repository secrets, so a PR trigger would fail every outside contribution.

**What it asserts is wiring, not quality.** Tests and evals are kept apart. The checks
themselves, and why there are these four, are in :mod:`tests.tier3.checks`.

**Routes, not a provider.** A pin stores pydantic-ai's normalized response, but the
*parsing* that produces it is per model class: a direct Anthropic call goes through
``AnthropicModel``, while OpenRouter — whichever model it routes to, Claude included —
goes through ``OpenRouterModel``, a subclass of ``OpenAIChatModel``, and comes back
OpenAI-shaped. Those are different real
shapes, and each route covers one the other cannot. So every route with a key runs, and
a route without one is skipped where everyone can see it.

**And at least one must run.** Every route skipping is a green run that called nothing
— a gate that passes by doing nothing, which is worse than no gate, because it is
believed. That check lives here, beside the route list, rather than in the workflow, so
adding a route cannot leave the guard behind and ``pytest -m tier3`` is as honest
locally as in CI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.models import infer_model, override_allow_model_requests

from tests.tier3.checks import CHECKS
from tests.tier3.transient import run_retrying_transient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from pydantic_ai.models import Model

pytestmark = pytest.mark.tier3


@dataclass(frozen=True, slots=True)
class Route:
    """A way of reaching a model, and the key that opens it."""

    name: str
    key: str
    #: A pydantic-ai model string. Overridable per route with ``TIER3_<NAME>_MODEL``,
    #: so trying another model needs no edit to this file.
    default_model: str
    #: Which pydantic-ai model class parses the responses — what this route covers.
    parses_with: str

    @property
    def model(self) -> str:
        # `or`, not a default: an empty override -- `FOO=` -- means unset, not "no model".
        return os.environ.get(f"TIER3_{self.name.upper()}_MODEL") or self.default_model

    @property
    def configured(self) -> bool:
        return bool(os.environ.get(self.key))


ROUTES = (
    Route(
        name="anthropic",
        key="ANTHROPIC_API_KEY",
        # The cheapest current Claude model, as on the other route: what this route covers
        # is `AnthropicModel`'s parsing, which every Claude model exercises. Passed all
        # four checks when chosen, the forced tool call included.
        default_model="anthropic:claude-haiku-5-5",
        parses_with="AnthropicModel",
    ),
    Route(
        name="openrouter",
        key="OPENROUTER_API_KEY",
        # OpenRouter's free router: $0 a token, and it picks whichever free model is
        # available, so no single model's withdrawal breaks the gate. Through OpenRouter
        # the response shape is the router's whichever model answers, so what this
        # route covers does not depend on the model routed to.
        #
        # A pinned free model was the default until OpenRouter withdrew it
        # (`nvidia/nemotron-3-super-120b-a12b:free` began answering 404). The cost of
        # the router is that each request may reach a different model, so a passing
        # run says nothing certain about the next one. Free models also draw on a
        # shared upstream pool. So when this route fails at release -- a 429, or a
        # check failing on this route alone, such as a routed model refusing the forced
        # tool choice -- first suspect the model, not this library. Set
        # TIER3_OPENROUTER_MODEL (a repository variable, in CI) to a cheap paid model --
        # `openrouter:mistralai/mistral-nemo` was about $0.03 per million tokens -- and
        # re-run. If it passes there, the router's pick was the cause. The whole tier
        # is a few thousand tokens.
        default_model="openrouter:openrouter/free",
        # `OpenRouterModel`, which subclasses `OpenAIChatModel`. An earlier draft said
        # `OpenAIChatModel` -- true of a model built by hand over `OpenRouterProvider`,
        # not of what `infer_model` builds -- and the assertion below caught it.
        parses_with="OpenRouterModel",
    ),
)


@pytest.fixture
def live_calls_allowed() -> Iterator[None]:
    """
    Lift the suite-wide no-live-model guard, for these tests only.

    `tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False` at import so a stray call
    in tiers 0-2 fails rather than silently billing anyone. That guard is right, and it
    also silently disarmed this tier once: the call was rejected before any provider was
    reached, so the release gate exercised nothing and said so to nobody.

    Scoped rather than flipped globally, and restored on the way out, so a tier-3 run in
    the same session cannot leave the guard down for anything that follows it.
    """
    with override_allow_model_requests(allow_model_requests=True):
        yield


def test_at_least_one_route_is_configured():
    """The guard: a tier-3 run where every route skipped must not report success."""
    if not any(route.configured for route in ROUTES):
        keys = ", ".join(route.key for route in ROUTES)
        pytest.fail(
            f"no route has a key, so every check would skip and tier 3 would pass having "
            f"called nothing. Set at least one of: {keys}."
        )


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(
            route,
            id=route.name,
            marks=pytest.mark.skipif(not route.configured, reason=f"{route.key} not set"),
        )
        for route in ROUTES
    ],
)
@pytest.mark.parametrize("check", CHECKS.values(), ids=CHECKS.keys())
def test_against_a_real_provider(
    route: Route,
    check: Callable[[Model, Path], None],
    tmp_path: Path,
    live_calls_allowed: None,
):
    model = infer_model(route.model)
    # What the route claims to cover, checked rather than trusted: an override such as
    # TIER3_OPENROUTER_MODEL=anthropic:... would otherwise make the "openrouter" route
    # test the other route's parser, and report it under the wrong name.
    assert type(model).__name__ == route.parses_with, (
        f"{route.name} is meant to cover {route.parses_with}; {route.model} parses with "
        f"{type(model).__name__}"
    )
    # A 502 or 429 from a shared provider pool is retried; see `transient.py`.
    run_retrying_transient(lambda directory: check(model, directory), tmp_path)
