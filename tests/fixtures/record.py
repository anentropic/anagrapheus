"""
Re-record the committed pin corpora.

``just record-fixtures`` re-records the scripted one — free, deterministic, no key. A
corpus recorded through a real route is re-recorded by hand, with that route's key::

    uv run python -m tests.fixtures.record --model openrouter:... --out tests/fixtures/...

and the exact command is written into the file's header, so a failing test can say it.

A committed recording is a regression test on **what the model is asked**. Change a
prompt — a brief line, the chronicle's render, a tool's description — and every pin
whose request changed misses on replay, so the test reading the recording fails. That
is the point, not a nuisance: change the prompt assembly and the keys miss, and this
is where that becomes visible in review, as a diff to this directory, instead of a
silent change in what a deployed agent is told.

So re-recording is deliberate, never automatic. Run it when a prompt changed on purpose,
read the diff, and commit the two together.

**A real-route corpus replays without its key.** A replay needs the model's *id* —
it is in every pin key — and not its credentials, because a strict replay raises at the
first miss, before anything is sent. :func:`replay_lineup` rebuilds the recorded models
with a placeholder key that is never transmitted, so the default suite replays real
response shapes on every commit with no secret in sight.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from examples.negotiation.lineup import Lineup
from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider_class

from anagrapheus.timeline import pin_events, write_recording

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

    from anagrapheus.timeline import Json

#: The scripted corpus, next to this module.
NEGOTIATION = Path(__file__).with_name("negotiation.pins.jsonl")

#: A corpus recorded through a real route, so real response shapes are replayed by the
#: default suite. Recorded on the cheapest model that passed every tier-3 check.
NEGOTIATION_LIVE = Path(__file__).with_name("negotiation.openrouter.pins.jsonl")

#: How to regenerate the scripted corpus.
RERECORD = "just record-fixtures"

#: What a placeholder credential says, should it ever appear anywhere it should not.
_NEVER_SENT = "replay-only-never-sent"


def record_negotiation(
    path: Path = NEGOTIATION,
    *,
    specs: Sequence[str] = (),
    show: Callable[[str], None] | None = None,
) -> int:
    """
    Play the four-round story and keep its pins. Scripted unless ``specs`` name models.

    ``show`` is handed each step as it is reached, so a live recording doubles as the
    live demo instead of costing a second run.
    """
    models = Lineup.parse(specs) if specs else Lineup.of(scripted())
    with tempfile.TemporaryDirectory() as directory:
        run = negotiate(Path(directory) / "summit.db", models=models)
        if show is not None:
            for step in run.steps:
                show(f"{step.label:>24}  {step.detail}")
        run.commit()
        header: dict[str, Json] = {
            "story": "examples.negotiation: two parties, four rounds",
            # What ran, slot by slot. A pin key includes the model id, so this corpus
            # replays only against these models; against any other, every draw misses.
            "lineup": run.lineup.describe(run.table.parties),
            "rerecord": _rerecord(path, specs),
        }
        if specs:
            # What was asked for, in the form `Lineup.parse` reads back. Kept beside the
            # lineup rather than recovered from it: the description names every slot per
            # party, which a lineup can only be rebuilt from with a default to spare.
            header["models"] = cast("Json", list(specs))
        written = write_recording(path, pin_events(run.table.store.read()), header=header)
        run.table.close()
    return written


def _rerecord(path: Path, specs: Sequence[str]) -> str:
    """The command that regenerates ``path``, as it should be typed from the repo root."""
    if not specs:
        return RERECORD
    shown = path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path
    models = " ".join(f"--model {spec}" for spec in specs)
    return f"uv run python -m tests.fixtures.record {models} --out {shown}  # needs the key"


def replay_lineup(header: Mapping[str, Json]) -> Lineup:
    """
    The models a real-route corpus was recorded with, rebuilt without their keys.

    The same name, so the same id and the same keys; a placeholder credential, because a
    strict replay never sends a request. The default suite also forbids live requests
    outright (``tests/conftest.py``), so a miss here could not reach a provider even if
    strict mode were turned off.

    **The id is what is proven necessary, and only the id.** The real model *class* is
    rebuilt too, through ``infer_model``, because a model's profile can change the
    request — a different schema transformer rewrites every tool's parameters, and those
    are in the key. But that is the safe choice rather than a demonstrated need: an
    ``OpenAIChatModel`` over the same provider, with the same id and a different class,
    replays this corpus just as well, because for these two classes the request comes
    out the same. An earlier draft of this docstring claimed the profile was needed too,
    and a mutant that swapped the class showed it was not.
    """
    specs = cast("list[str]", header["models"])

    def keyless(provider: str) -> Provider[object]:
        # Every concrete provider takes `api_key`; the base class does not declare it,
        # so this is the one place the assumption is made, and it is made out loud.
        make = cast("Callable[..., Provider[object]]", infer_provider_class(provider))
        return make(api_key=_NEVER_SENT)

    def resolve(spec: str) -> Model:
        return infer_model(spec, provider_factory=keyless)

    return Lineup.parse(specs, resolve=resolve)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.fixtures.record")
    parser.add_argument("--model", action="append", default=[], metavar="SPEC")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    specs: list[str] = args.model
    if specs and args.out is None:
        parser.error("--out is required with --model: a real route gets a corpus of its own")
    path: Path = args.out or NEGOTIATION
    written = record_negotiation(path, specs=specs, show=print if specs else None)
    print(f"wrote {written} pins to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
