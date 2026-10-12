"""
``python -m examples.negotiation`` — the demo, run.

Prints exactly what ``tests/test_acceptance_m8.py`` asserts on, because both read the
same :class:`~examples.negotiation.story.Negotiation`. A demo that renders its own
narration separately from what is tested drifts within a milestone or two into
describing behaviour the code no longer has.

With no ``--model`` it runs the scripted counterpart, offline. Each ``--model`` puts a
real model in a slot, so the same command compares models per role::

    python -m examples.negotiation --model anthropic:claude-opus-5
    python -m examples.negotiation --model openrouter:openai/gpt-5 --model update=...
    python -m examples.negotiation --model ... --model eu.propose=google:gemini-3-pro

A bare spec is the default for every slot; ``role=`` and ``party.role=`` override it.
Roles are ``propose``, ``update`` and ``summarize``. Model strings are pydantic-ai's,
so any provider it knows works, and a missing API key is reported before anything runs.

Worth running against a real model even once: *"the replay reached no model"* is
unremarkable against a :class:`~pydantic_ai.models.function.FunctionModel` and striking
against a provider, and it is the only way to see this stack's prompts meet response
shapes nothing synthetic produces.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from examples.negotiation.agent import UnrecordableOutput
from examples.negotiation.lineup import ROLES, Lineup, LineupError
from examples.negotiation.scripted import scripted
from examples.negotiation.story import PARTIES, negotiate
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError

from anagrapheus.timeline import PinMiss, ReplayPins

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Play the negotiation, print it, then replay it on a fresh store and say what it cost."""
    parser = argparse.ArgumentParser(
        prog="python -m examples.negotiation",
        description="The negotiation demo: two agents, four rounds, then a replay.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="[[PARTY.]ROLE=]PROVIDER:MODEL",
        help=(
            "a real model for a slot; repeatable. A bare spec fills every slot, "
            f"ROLE= (one of {', '.join(ROLES)}) or PARTY.ROLE= overrides it. "
            "Omit entirely to run the scripted counterpart offline."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help=(
            "log backend activity to stderr: INFO shows every model and tool call and "
            "each round's steps, DEBUG adds event appends, belief writes and folds"
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    specs: list[str] = args.model
    try:
        lineup = Lineup.parse(specs) if specs else Lineup.of(scripted())
        lineup.check_parties(PARTIES)
    except (LineupError, UserError) as error:
        # Both are a model that cannot fill its slot -- a malformed spec, an unknown
        # provider, a missing key, a party not at the table -- and both are reported
        # here, before the first draw, rather than as a traceback from inside round one.
        parser.error(str(error))

    with tempfile.TemporaryDirectory() as directory:
        try:
            run = negotiate(Path(directory) / "summit.db", models=lineup)
        except (UnrecordableOutput, UnexpectedModelBehavior) as error:
            # A model that answered with something the round cannot record, and kept
            # doing so after being told what was expected. Ordinary for a model, so
            # reported as what it is rather than as a traceback from the round loop.
            print(f"a model's answer could not be recorded: {error}", file=sys.stderr)
            return 1
        for step in run.steps:
            print(f"{step.label:>24}  {step.detail}")

        # Promote the pins into the log, which is what makes the replay below possible
        # at all: until `commit`, every draw lives in the scratch and nothing canon
        # records that it happened.
        run.commit()
        events = list(run.table.store.read())
        run.table.close()

        # The same story, a *fresh* store, and every draw answered from the log. Strict
        # mode raises at the first miss, so a replay that finishes reached no model --
        # by construction, which is a stronger claim than a count. An earlier version
        # printed a count that only the scripted model ever incremented, so against a
        # real provider it said 0 whether or not anything had been reached.
        print()
        try:
            replayed = negotiate(
                Path(directory) / "replay.db",
                models=lineup,
                resolver=lambda table: ReplayPins.from_events(
                    events, table.types, on_miss="strict"
                ),
            )
        except PinMiss as miss:
            print(f"replay diverged: {miss}")
            return 1
        print(
            f"replayed {len(replayed.rounds)} rounds on a fresh store in strict mode: "
            "every draw answered from the log, none from a model"
        )
        replayed.table.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
