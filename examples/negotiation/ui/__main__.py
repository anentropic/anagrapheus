"""
``python -m examples.negotiation.ui`` — the negotiation, watched in a browser.

Takes the same ``--model`` specs as ``python -m examples.negotiation`` and starts a
local server; the page has a Run button and a box to change the lineup between runs.
With no ``--model`` the scripted counterpart plays, offline::

    python -m examples.negotiation.ui
    python -m examples.negotiation.ui --model anthropic:claude-haiku-5-5

Backend activity is logged to the terminal at ``--log-level`` as well as streamed to
the page, which always receives everything down to DEBUG.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from typing import TYPE_CHECKING

# Before pydantic-ai is imported: its banner would otherwise interrupt the terminal log.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from examples.negotiation.ui.server import serve  # noqa: E402
from examples.negotiation.ui.session import WATCHED_LOGGERS, Hub, Runner  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Start the server, open the page, and run a negotiation if asked to."""
    parser = argparse.ArgumentParser(
        prog="python -m examples.negotiation.ui",
        description="Watch the negotiation demo: beliefs, derived models and model calls.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="[[PARTY.]ROLE=]PROVIDER:MODEL",
        help="as for `python -m examples.negotiation`; prefills the page's lineup",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument(
        "--run", action="store_true", help="start a negotiation as soon as the server is up"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="what reaches the terminal; the page always gets everything",
    )
    args = parser.parse_args(argv)

    terminal = logging.StreamHandler()
    terminal.setLevel(args.log_level)
    terminal.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    )
    hub = Hub()
    runner = Runner(hub)
    for name in WATCHED_LOGGERS:
        logger = logging.getLogger(name)
        # DEBUG on the loggers themselves, so model calls build their `detail` payload;
        # each handler filters to its own level.
        logger.setLevel(logging.DEBUG)
        logger.addHandler(runner)
        logger.addHandler(terminal)
        logger.propagate = False

    try:
        server = serve(hub, runner, args.model, host=args.host, port=args.port)
    except OSError as error:
        print(f"could not listen on {args.host}:{args.port}: {error}", file=sys.stderr)
        return 1
    url = f"http://{args.host}:{args.port}/"
    print(f"negotiation UI at {url}  (ctrl-c to stop)", file=sys.stderr)

    if args.run and (error := runner.start(args.model)) is not None:
        parser.error(error)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
