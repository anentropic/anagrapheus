"""
Re-record the committed pin corpora: ``just record-fixtures``.

A committed recording is a regression test on **what the model is asked**. Change a
prompt — a brief line, the chronicle's render, a tool's description — and every pin
whose request changed misses on replay, so the test reading the recording fails. That
is the point, not a nuisance: doc 00 §4c says *"change the prompt assembly and the keys
miss"*, and this is where that becomes visible in review, as a diff to this directory,
instead of a silent change in what a deployed agent is told.

So re-recording is deliberate, never automatic. Run it when a prompt changed on purpose,
read the diff, and commit the two together.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate

from anagrapheus.timeline import pin_events, write_recording

#: Where the negotiation's corpus lives, next to this module.
NEGOTIATION = Path(__file__).with_name("negotiation.pins.jsonl")

#: How to regenerate it, written into the file itself so a failing test can say so.
RERECORD = "just record-fixtures"


def record_negotiation(path: Path = NEGOTIATION) -> int:
    """Play the four-round story on the scripted counterpart and keep its pins."""
    with tempfile.TemporaryDirectory() as directory:
        run = negotiate(Path(directory) / "summit.db", models=scripted())
        run.commit()
        header = {
            "story": "examples.negotiation: two parties, four rounds",
            # The route, slot by slot. A pin key includes the model id, so this corpus
            # replays only against these models; read against any other lineup, every
            # draw misses.
            "lineup": run.lineup.describe(run.table.parties),
            "rerecord": RERECORD,
        }
        written = write_recording(path, pin_events(run.table.store.read()), header=header)
        run.table.close()
    return written


if __name__ == "__main__":
    print(f"wrote {record_negotiation()} pins to {NEGOTIATION}")
