"""
``python -m examples.negotiation`` — the demo, run.

Prints exactly what ``tests/test_acceptance_m8.py`` asserts on, because both read the
same :class:`~examples.negotiation.story.Negotiation`. A demo that renders its own
narration separately from what is tested drifts within a milestone or two into
describing behaviour the code no longer has.

``--live`` swaps the scripted counterpart for a real provider. Worth having: *"the
replay reached no model"* is unremarkable against a
:class:`~pydantic_ai.models.function.FunctionModel` and striking against Anthropic, and
it is the only way to see this stack's prompts meet response shapes nothing synthetic
produces.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate

from anagrapheus.timeline import ReplayPins

if TYPE_CHECKING:
    from pydantic_ai.models import Model


def main() -> None:
    """Play the negotiation, print it, then replay it and print what that cost."""
    parser = argparse.ArgumentParser(prog="examples.negotiation", description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="draw through Anthropic instead of the scripted counterpart",
    )
    parser.add_argument(
        "--model",
        default="claude-opus-5",
        help="the model to use with --live",
    )
    args = parser.parse_args()

    calls: list[str] = []
    model = _live(args.model) if args.live else scripted(calls.append)

    with tempfile.TemporaryDirectory() as directory:
        run = negotiate(Path(directory) / "summit.db", model=model)
        for step in run.steps:
            print(f"{step.label:>24}  {step.detail}")

        # Promote the pins into the log, which is what makes the replay below possible
        # at all: until `commit`, every draw lives in the scratch and nothing canon
        # records that it happened.
        run.commit()
        drawn = len(calls)
        events = list(run.table.store.read())
        run.table.close()

        # The same story, a *fresh* store, and every draw answered from the log.
        replayed = negotiate(
            Path(directory) / "replay.db",
            model=model,
            resolver=lambda table: ReplayPins.from_events(events, table.types, on_miss="strict"),
        )
        print()
        print(
            f"replayed {len(replayed.rounds)} rounds; model calls during replay: "
            f"{len(calls) - drawn}"
        )
        # `divergence` is `ReplayPins`' own: a `PinResolver` promises resolution, not a
        # record of where a replay drifted from what was pinned.
        pins = replayed.pins
        print(f"divergence: {pins.divergence if isinstance(pins, ReplayPins) else 'n/a'}")
        replayed.table.close()


def _live(name: str) -> Model:
    """The provider, imported late so an offline run never needs the SDK configured."""
    from pydantic_ai.models.anthropic import AnthropicModel

    return AnthropicModel(name)


if __name__ == "__main__":
    main()
