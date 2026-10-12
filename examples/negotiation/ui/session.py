"""
A negotiation played on a worker thread, narrated as a stream of messages.

The browser sees one append-only list of JSON messages, held by a :class:`Hub`:

- ``run`` -- a run started, with the lineup it was asked for. A client resets here.
- ``frame`` -- one append to the log: the events it added, and a :func:`snapshot` of
  the table straight after. Frames are numbered per *stage*, ``live`` or ``replay``.
- ``log`` -- one log record from the library or the example, stamped with the stage and
  the frame it happened after, so scrubbing back in time can show what had happened so
  far. Model calls carry their conversation and answer as ``detail``.
- ``steps`` -- the story's own narration, once a stage finishes.
- ``done`` / ``failed`` -- how the run ended.

**Two stages, as the command-line demo runs them.** The live run draws from the models
and commits its pins; the replay plays the same story on a *fresh* store with every draw
answered from the log in strict mode. Watching the replay is the point: the same frames,
the same beliefs, and a log of model calls that all say "reused from pin".

Append-only and never trimmed. A client that reconnects resumes from the id it last
saw, and one that loads late replays the whole history -- both cheap, for a demo whose
longest run is a few hundred messages.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from examples.negotiation.agent import UnrecordableOutput
from examples.negotiation.lineup import Lineup, LineupError
from examples.negotiation.scripted import scripted
from examples.negotiation.story import PARTIES, negotiate
from examples.negotiation.ui.snapshot import Labeler, render_event, snapshot
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError

from anagrapheus.timeline import PinMiss, ReplayPins

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from examples.negotiation.table import Table

    from anagrapheus.timeline import PendingEvent

__all__ = ["Hub", "Runner", "WATCHED_LOGGERS"]

#: Whose log records reach the browser: the library's, and the example's own.
WATCHED_LOGGERS = ("anagrapheus", "examples.negotiation")

type Message = dict[str, Any]

#: The round loop's step announcements -- "round 2: read -- ..." -- and nothing else.
_STEP = re.compile(r"^(round \d+: (?:act|say|leak|read|close))(?: --|$)")


@dataclass
class _Watched:
    """One table being watched: its labels, its stage, and how far it has been framed."""

    table: Table
    labels: Labeler
    stage: str
    seen: int = 0


class Hub:
    """The append-only message list, and a way to wait for more of it."""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._changed = threading.Condition()

    def publish(self, message: Message) -> None:
        with self._changed:
            message["n"] = len(self._messages)
            self._messages.append(message)
            self._changed.notify_all()

    def __len__(self) -> int:
        with self._changed:
            return len(self._messages)

    def since(self, n: int, *, timeout: float) -> list[Message]:
        """Messages from index ``n`` on, waiting up to ``timeout`` if there are none."""
        with self._changed:
            self._changed.wait_for(lambda: len(self._messages) > n, timeout=timeout)
            return self._messages[n:]


class Runner(logging.Handler):
    """
    Plays negotiations one at a time and publishes everything they do.

    Also the log handler, because a log record is only useful on screen if it knows
    *when* it happened in story terms -- which stage, after which frame -- and the
    runner is what knows that.
    """

    #: The table being framed right now; set by the ``observe`` hook as each stage opens.
    _watched: _Watched

    def __init__(self, hub: Hub) -> None:
        super().__init__(level=logging.DEBUG)
        self._hub = hub
        self._lock = threading.Lock()
        self._busy = False
        self._run = 0
        self._stage = "live"
        self._frames = {"live": -1, "replay": -1}
        self._phase = ""

    # ---- starting a run ----------------------------------------------------------------

    def start(self, specs: Sequence[str]) -> str | None:
        """
        Start a run with these ``--model`` specs, or the scripted model if there are none.

        Returns an error to show instead, when the lineup is malformed or a run is
        already going. Checked here, on the request thread, so a missing API key is
        reported to the person who clicked rather than as a failed run.
        """
        try:
            lineup = Lineup.parse(specs) if specs else Lineup.of(scripted())
            lineup.check_parties(PARTIES)
        except (LineupError, UserError) as error:
            return str(error)
        with self._lock:
            if self._busy:
                return "a negotiation is already running; wait for it to finish"
            self._busy = True
            self._run += 1
        threading.Thread(
            target=self._play, args=(lineup, list(specs), self._run), daemon=True
        ).start()
        return None

    def _play(self, lineup: Lineup, specs: list[str], run: int) -> None:
        self._frames = {"live": -1, "replay": -1}
        self._phase = ""
        self._hub.publish(
            {
                "type": "run",
                "run": run,
                "specs": specs or ["scripted (offline)"],
                "t": time.time(),
            }
        )
        try:
            self._hub.publish({"type": "done", "run": run, **self._stages(lineup)})
        except (UnrecordableOutput, UnexpectedModelBehavior, PinMiss) as error:
            self._fail(run, f"{type(error).__name__}: {error}", None)
        except Exception as error:  # noqa: BLE001 -- shown in the browser, and logged
            logging.getLogger(__name__).exception("the run failed")
            self._fail(run, f"{type(error).__name__}: {error}", traceback.format_exc())
        finally:
            with self._lock:
                self._busy = False

    def _stages(self, lineup: Lineup) -> dict[str, Any]:
        """Play live, commit, then replay on a fresh store -- as ``__main__`` does."""
        with tempfile.TemporaryDirectory() as directory:
            self._stage = "live"
            played = negotiate(
                Path(directory) / "summit.db", models=lineup, observe=self._watch("live")
            )
            self._steps("live", played.steps)
            played.commit()
            # Promotion appends through the store, not the table, so no listener sees
            # it; and the last round's consolidation landed after the last append. One
            # closing frame carries both.
            self._settle(self._watched)
            events = list(played.table.store.read())
            played.table.close()

            self._stage = "replay"
            self._phase = ""
            replayed = negotiate(
                Path(directory) / "replay.db",
                models=lineup,
                resolver=lambda table: ReplayPins.from_events(
                    events, table.types, on_miss="strict"
                ),
                observe=self._watch("replay"),
            )
            self._steps("replay", replayed.steps)
            self._settle(self._watched)
            replayed.table.close()
        return {
            "verdict": (
                f"replayed {len(replayed.rounds)} rounds on a fresh store in strict mode: "
                "every draw answered from the log, none from a model"
            )
        }

    def _fail(self, run: int, message: str, trace: str | None) -> None:
        self._hub.publish({"type": "failed", "run": run, "error": message, "trace": trace})

    def _steps(self, stage: str, steps: Sequence[Any]) -> None:
        self._hub.publish(
            {
                "type": "steps",
                "stage": stage,
                "steps": [{"label": s.label, "detail": s.detail} for s in steps],
            }
        )

    # ---- watching the table ------------------------------------------------------------

    def _watch(self, stage: str) -> Callable[[Table], None]:
        """The ``observe`` hook: label the table's events and frame every append."""

        def observe(table: Table) -> None:
            watched = self._watched = _Watched(table, Labeler(table), stage)
            # Seating happens inside `Table.open`, before this hook runs.
            self._catch_up(watched)
            table.on_append.append(lambda events, last: self._appended(watched, events, last))

        return observe

    def _appended(self, watched: _Watched, events: tuple[PendingEvent, ...], last: int) -> None:
        first = last - len(events) + 1
        for offset, event in enumerate(events):
            watched.labels.saw(first + offset, event.envelope.event_id, event.envelope.type)
        rendered = [render_event(e, first + i, watched.labels) for i, e in enumerate(events)]
        self._frame(watched, rendered, last)

    def _settle(self, watched: _Watched) -> None:
        """One closing frame, events or not: projections move after the last append."""
        self._catch_up(watched, always=True)

    def _catch_up(self, watched: _Watched, *, always: bool = False) -> None:
        """Frame whatever reached the store without passing through ``Table.append``."""
        fresh = list(watched.table.store.read(since_seq=watched.seen))
        if not fresh:
            if always:
                self._frame(watched, [], watched.seen)
            return
        for event in fresh:
            watched.labels.saw(event.seq, event.envelope.event_id, event.envelope.type)
        rendered = [render_event(e, e.seq, watched.labels) for e in fresh]
        self._frame(watched, rendered, fresh[-1].seq)

    def _frame(self, watched: _Watched, events: list[dict[str, Any]], last: int) -> None:
        watched.seen = last
        self._frames[watched.stage] += 1
        self._hub.publish(
            {
                "type": "frame",
                "stage": watched.stage,
                "index": self._frames[watched.stage],
                "seq": last,
                "phase": self._phase,
                "events": events,
                "snapshot": snapshot(watched.table, watched.labels),
                "t": time.time(),
            }
        )

    # ---- logging.Handler ---------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.name == "examples.negotiation.rounds" and (step := _STEP.match(message)):
                # A round's step announcements double as the frame's phase label.
                self._phase = step.group(1)
            self._hub.publish(
                {
                    "type": "log",
                    "stage": self._stage,
                    "frame": self._frames[self._stage],
                    "t": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                    "detail": getattr(record, "detail", None),
                }
            )
        except Exception:  # noqa: BLE001 -- the logging module's own contract
            self.handleError(record)
